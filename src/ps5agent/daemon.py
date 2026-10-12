"""Long-lived daemon: owns the single Remote Play session, serves a loopback HTTP API."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import signal
import time

from aiohttp import web

from . import frames
from .backend import Button, ConsoleBackend, ConsoleError, ConsoleState, Side, make_backend
from .config import Config
from .engine import InputEngine, Step

_LOG = logging.getLogger(__name__)
STALE_FRAME_S = 2.0
SHUTDOWN = web.AppKey("shutdown", asyncio.Event)
BACKOFF_MAX_S = 30.0


class Daemon:
    def __init__(self, cfg: Config, backend: ConsoleBackend | None = None) -> None:
        self.cfg = cfg
        self.backend = backend or make_backend(cfg)
        self.engine = InputEngine(self.backend, cfg.max_hold_ms)
        self.state = "starting"  # starting | connecting | up | reconnecting | standby | unreachable
        self.last_error = ""
        self.started = time.monotonic()
        self._supervisor: asyncio.Task | None = None
        self._wakeup = asyncio.Event()
        self._stopping = False
        self._was_up = False

    # --- session supervision ---------------------------------------------
    async def start(self) -> None:
        self.engine.start()
        self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        self._stopping = True
        if self._supervisor:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
        await self.engine.stop()
        with contextlib.suppress(Exception):
            await self.backend.disconnect()

    def kick(self) -> None:
        """Re-check the session now instead of waiting out the backoff."""
        self._wakeup.set()

    async def _sleep(self, seconds: float) -> None:
        self._wakeup.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wakeup.wait(), seconds)

    async def _supervise(self) -> None:
        backoff = 1.0
        while not self._stopping:
            try:
                status = await self.backend.status()
            except Exception as err:
                self.state, self.last_error = "unreachable", str(err)
                await self._sleep(5)
                continue
            if status.session_up:
                self.state, self._was_up, backoff = "up", True, 1.0
                await self._sleep(1)
                continue
            if self._was_up:
                # session dropped under us: make sure nothing is left pressed
                await self.engine.release_all()
                self._was_up = False
            if status.console is ConsoleState.STANDBY:
                self.state, self.last_error = "standby", ""
                await self._sleep(5)
                continue
            if status.console is ConsoleState.UNREACHABLE:
                self.state = "unreachable"
                await self._sleep(5)
                continue
            self.state = "reconnecting" if self.state in ("up", "reconnecting") else "connecting"
            try:
                await self.backend.connect()
                self.last_error = ""
                continue
            except Exception as err:
                self.last_error = str(err) or type(err).__name__
                _LOG.warning("connect failed (%s); retry in %.0f s", self.last_error, backoff)
            await self._sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    # --- views -----------------------------------------------------------
    async def status_dict(self) -> dict:
        status = await self.backend.status()
        frame = self.backend.latest_frame()
        age = round(time.monotonic() - frame.ts, 2) if frame else None
        return {
            "daemon": self.state,
            "console": status.console.value,
            "session_up": status.session_up,
            "fps": round(status.fps, 1),
            "frame_age_s": age,
            "firmware": status.firmware,
            "app": status.app,
            "last_error": self.last_error or status.detail,
            "watchdog_trips": self.engine.watchdog_trips,
            "backend": self.cfg.backend,
        }

    async def wait_ready(self, timeout_s: float = 20.0) -> None:
        """Let a request that arrives while the session is (re)connecting wait for it, not fail."""
        deadline = time.monotonic() + timeout_s
        self.kick()  # skip a pending backoff once; not in the loop, or we would hammer the console
        # polling a plain state attribute the supervisor owns; an Event per transition is not worth it
        while self.state in ("starting", "connecting", "reconnecting") and time.monotonic() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.2)

    async def snapshot(self, max_width: int = 1024, quality: int = 80) -> dict:
        await self.wait_ready()
        frame = self.backend.latest_frame()
        if frame is None:
            raise ConsoleError(f"no frame available (daemon state: {self.state}; {self.last_error})")
        jpeg, width, height = await asyncio.to_thread(frames.to_jpeg, frame.rgb, max_width, quality)
        age = time.monotonic() - frame.ts
        warnings = []
        if age > STALE_FRAME_S:
            warnings.append(
                f"frame is {age:.1f} s old - stream stalled or reconnecting (state: {self.state})"
            )
        if await asyncio.to_thread(frames.is_black, frame.rgb):
            warnings.append("frame is black - content may be blocked from Remote Play, or screen is off")
        return {
            "jpeg_b64": base64.b64encode(jpeg).decode(),
            "frame_id": frame.frame_id,
            "age_ms": round(age * 1000),
            "width": width,
            "height": height,
            "warnings": warnings,
        }

    async def latency(self, samples: int = 10) -> dict:
        """Median press -> visible change, alternating right/left (home screen is a good bench)."""
        results = []
        for i in range(samples):
            before = self.backend.latest_frame()
            if before is None:
                raise ConsoleError("no frames")
            base = await asyncio.to_thread(frames.small_gray, before.rgb)
            t0 = time.monotonic()
            await self.engine.press(Button.RIGHT if i % 2 == 0 else Button.LEFT, 80)
            seen = before.frame_id
            while time.monotonic() - t0 < 3.0:
                cur = self.backend.latest_frame()
                if cur and cur.frame_id != seen:
                    seen = cur.frame_id
                    gray = await asyncio.to_thread(frames.small_gray, cur.rgb)
                    if frames.changed_fraction(base, gray) > 0.002:
                        results.append((cur.ts - t0) * 1000)
                        break
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.8)
        results.sort()
        median = results[len(results) // 2] if results else None
        return {"samples_ms": [round(r) for r in results], "median_ms": median and round(median)}


# --- HTTP API ------------------------------------------------------------
def _side(value) -> Side:
    return Side(str(value).lower())


def build_app(daemon: Daemon) -> web.Application:
    routes = web.RouteTableDef()

    async def after(body: dict, result: dict) -> dict:
        ms = body.get("snapshot_after_ms")
        if ms is not None:
            await asyncio.sleep(max(0, min(int(ms), 10_000)) / 1000)
            result["snapshot"] = await daemon.snapshot(
                int(body.get("max_width", 1024)), int(body.get("quality", 80))
            )
        return result

    @routes.get("/status")
    async def status(request: web.Request) -> web.Response:
        return web.json_response(await daemon.status_dict())

    @routes.get("/snapshot")
    async def snapshot(request: web.Request) -> web.Response:
        q = request.query
        return web.json_response(
            await daemon.snapshot(int(q.get("max_width", 1024)), int(q.get("quality", 80)))
        )

    @routes.post("/input")
    async def input_(request: web.Request) -> web.Response:
        body = await request.json()
        op = body.get("op")
        if op == "release_all":
            await daemon.engine.release_all()
        elif op == "sequence":
            steps = [Step.parse(s) for s in body.get("steps", [])]
            await daemon.wait_ready()
            await daemon.engine.run(steps)
        elif op in ("press", "hold", "stick", "trigger"):
            step = Step.parse(body)
            await daemon.wait_ready()
            await daemon.engine.run([step])
        else:
            raise ValueError(f"unknown op {op!r}")
        return web.json_response(await after(body, {"ok": True}))

    @routes.post("/wake")
    async def wake(request: web.Request) -> web.Response:
        await daemon.backend.wake()
        daemon.kick()
        return web.json_response({"ok": True})

    @routes.post("/standby")
    async def standby(request: web.Request) -> web.Response:
        await daemon.engine.release_all()
        await daemon.backend.standby()
        daemon.kick()
        return web.json_response({"ok": True})

    @routes.post("/latency")
    async def latency(request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else {}
        return web.json_response(await daemon.latency(int(body.get("samples", 10))))

    @routes.post("/shutdown")
    async def shutdown(request: web.Request) -> web.Response:
        asyncio.get_running_loop().call_later(0.1, request.app[SHUTDOWN].set)
        return web.json_response({"ok": True})

    @web.middleware
    async def errors(request: web.Request, handler) -> web.StreamResponse:
        try:
            return await handler(request)
        except (ValueError, KeyError) as err:
            return web.json_response({"ok": False, "error": f"bad request: {err}"}, status=400)
        except ConsoleError as err:
            return web.json_response({"ok": False, "error": str(err)}, status=409)

    app = web.Application(middlewares=[errors])
    app.add_routes(routes)
    app[SHUTDOWN] = asyncio.Event()
    return app


def make_runner(app: web.Application) -> web.AppRunner:
    # handler_cancellation: a client that disconnects mid-hold cancels the handler,
    # and the engine's cleanup releases the input right away instead of at hold end.
    return web.AppRunner(app, handle_signals=False, handler_cancellation=True)


async def serve(cfg: Config, backend: ConsoleBackend | None = None) -> None:
    daemon = Daemon(cfg, backend)
    app = build_app(daemon)
    runner = make_runner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.daemon_host, cfg.daemon_port)
    await site.start()
    await daemon.start()
    _LOG.info("daemon listening on %s (backend %s)", cfg.daemon_url, cfg.backend)

    loop = asyncio.get_running_loop()
    stop = app[SHUTDOWN]
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    try:
        await stop.wait()
    finally:
        _LOG.info("shutting down: releasing input and closing the session")
        await daemon.stop()
        await runner.cleanup()


def run(cfg: Config) -> None:
    async def main() -> None:
        code = 0
        try:
            await serve(cfg)
        except Exception:
            _LOG.exception("daemon crashed")
            code = 1
        finally:
            # pyremoteplay leaves a pending UDP overlapped op; asyncio.run's loop close would
            # then hang forever on Windows, so exit from inside the loop once cleanup is done.
            logging.shutdown()
            os._exit(code)

    asyncio.run(main())
