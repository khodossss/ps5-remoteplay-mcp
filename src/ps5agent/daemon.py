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
from .backend import Button, ConsoleBackend, ConsoleError, ConsoleState, Frame, Side, make_backend
from .config import Config
from .engine import InputEngine, Step
from .pad_bridge import PadBridge
from .pad_reader import PadReader

_LOG = logging.getLogger(__name__)
STALE_FRAME_S = 2.0
STALL_RECONNECT_S = 10.0  # a session with no video this long is treated as dead
SHUTDOWN = web.AppKey("shutdown", asyncio.Event)
BACKOFF_MAX_S = 30.0
WAITING_STATES = ("starting", "connecting", "reconnecting", "stalled")
CHANGE_FRACTION = 0.002  # share of downscaled pixels that must differ to count as a screen change
CHANGE_SETTLE_S = 0.4  # after the first change, let the transition finish before the snapshot
FRESH_FRAME_WAIT_S = 2.0
FRAME_PATH = "/frame"


class Daemon:
    def __init__(self, cfg: Config, backend: ConsoleBackend | None = None) -> None:
        self.cfg = cfg
        self.backend = backend or make_backend(cfg)
        self.engine = InputEngine(self.backend, cfg.max_hold_ms)
        self.pad = PadBridge(self)
        self.pad_reader: PadReader | None = None
        # starting | connecting | up | stalled | reconnecting | standby | unreachable | not_set_up
        self.state = "starting"
        self.last_error = ""
        self.started = time.monotonic()
        self.last_call = time.monotonic()
        self.ready_timeout_s = 20.0  # how long input waits for a live stream before it is refused
        self._supervisor: asyncio.Task | None = None
        self._wakeup = asyncio.Event()
        self._stopping = False
        self._was_up = False
        self.session_epoch = 0  # bumped on every session loss

    # --- session supervision ---------------------------------------------
    async def start(self) -> None:
        self.engine.start()
        self.pad.start()
        if self.cfg.backend != "fake":
            loop = asyncio.get_running_loop()
            self.pad_reader = PadReader(
                lambda state: loop.call_soon_threadsafe(self.pad.submit, state, time.monotonic())
            )
            self.pad_reader.start()
        self._supervisor = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        self._stopping = True
        if self.pad_reader:
            await asyncio.to_thread(self.pad_reader.stop)
        await self.pad.stop()
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
                if self.pad.active:
                    await self.pad.reset()
                await self._sleep(5)
                continue
            if status.session_up:
                frame_age = None if status.last_frame_ts is None else time.monotonic() - status.last_frame_ts
                stale = frame_age is None or frame_age > STALE_FRAME_S
                self.state, self._was_up, backoff = ("stalled" if stale else "up"), True, 1.0
                if stale and self.pad.active:
                    await self.pad.reset()
                if frame_age is not None and frame_age > STALL_RECONNECT_S:
                    self.last_error = f"no video for {frame_age:.0f} s; reconnecting"
                    _LOG.warning("%s", self.last_error)
                    with contextlib.suppress(Exception):
                        await self.backend.disconnect()
                await self._sleep(0.5)
                continue
            if self._was_up:
                # session dropped under us: make sure nothing is left pressed, and end the epoch so
                # an input still running cannot continue into the next session
                await self.pad.reset()
                self._was_up = False
                self.session_epoch += 1
            if status.console is ConsoleState.STANDBY:
                self.state, self.last_error = "standby", ""
                await self._sleep(5)
                continue
            if status.console in (ConsoleState.UNREACHABLE, ConsoleState.NOT_SET_UP):
                self.state, self.last_error = status.console.value, status.detail
                await self._sleep(5)
                continue
            self.state = "reconnecting" if self.state in ("up", "stalled", "reconnecting") else "connecting"
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
            "gamepad": self.pad_reader.name if self.pad_reader else None,
            "human_input": self.pad.active,
        }

    async def wait_ready(self, timeout_s: float = 20.0) -> bool:
        """Let a request that arrives while the session is (re)connecting or stalled wait for a live
        stream, not fail. True when the stream is live."""
        deadline = time.monotonic() + timeout_s
        self.kick()  # skip a pending backoff once; not in the loop, or we would hammer the console
        # polling a plain state attribute the supervisor owns; an Event per transition is not worth it
        while self.state in WAITING_STATES and time.monotonic() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.2)
        return self.state == "up"

    async def require_ready(self) -> None:
        """Input is sent only into a live stream: never queued for a session that comes back later."""
        self.pad.require_agent()
        if not await self.wait_ready(self.ready_timeout_s):
            raise ConsoleError(
                f"input not sent: the stream is not live (state: {self.state}"
                + (f"; {self.last_error}" if self.last_error else "")
                + "). Check `status` or call `wait_ready`."
            )

    def check_input(self, epoch: int) -> None:
        if self.session_epoch != epoch:
            raise ConsoleError("the Remote Play session was lost during this input")
        self.pad.require_agent()

    async def snapshot(
        self,
        max_width: int = 1024,
        quality: int = 80,
        before: Frame | None = None,
        sent_at: float | None = None,
    ) -> dict:
        """The latest frame. With `before`/`sent_at` (a snapshot after input) it also says whether the
        screen differs from before the input and warns when no frame was decoded since the input."""
        await self.wait_ready()
        frame = self.backend.latest_frame()
        if frame is None:
            if self.state == "not_set_up":
                raise ConsoleError("the PS5 is not set up yet: call the `setup` tool first")
            raise ConsoleError(f"no frame available (daemon state: {self.state}; {self.last_error})")
        jpeg, width, height = await asyncio.to_thread(frames.to_jpeg, frame.rgb, max_width, quality)
        age = time.monotonic() - frame.ts
        warnings = []
        if age > STALE_FRAME_S:
            warnings.append(
                f"frame is {age:.1f} s old - stream stalled or reconnecting (state: {self.state})"
            )
        if sent_at is not None and frame.ts <= sent_at:
            warnings.append("no frame decoded since the input - this picture may predate it")
        if await asyncio.to_thread(frames.is_black, frame.rgb):
            warnings.append("frame is black - content may be blocked from Remote Play, or screen is off")
        result = {
            "jpeg_b64": base64.b64encode(jpeg).decode(),
            "frame_id": frame.frame_id,
            "age_ms": round(age * 1000),
            "width": width,
            "height": height,
            "warnings": warnings,
        }
        if before is not None:
            result["screen_changed"] = await self._differs(before, frame)
        return result

    @staticmethod
    async def _differs(a: Frame, b: Frame) -> bool:
        if a.frame_id == b.frame_id:
            return False
        ga, gb = await asyncio.gather(
            asyncio.to_thread(frames.small_gray, a.rgb), asyncio.to_thread(frames.small_gray, b.rgb)
        )
        return frames.changed_fraction(ga, gb) > CHANGE_FRACTION

    async def await_after_input(
        self, before: Frame | None, sent_at: float, wait_ms: int, until_change: bool
    ) -> None:
        """Wait `wait_ms` after the input; with `until_change`, stop early once the screen has changed
        (plus a short settle). Then wait for a frame decoded after the input."""
        deadline = sent_at + wait_ms / 1000
        seen = before.frame_id if before else None
        while time.monotonic() < deadline:
            if until_change and before is not None:
                cur = self.backend.latest_frame()
                if cur is not None and cur.frame_id != seen:
                    seen = cur.frame_id
                    if await self._differs(before, cur):
                        await asyncio.sleep(CHANGE_SETTLE_S)
                        break
            await asyncio.sleep(0.05 if until_change else max(0.0, deadline - time.monotonic()))
        fresh_deadline = time.monotonic() + FRESH_FRAME_WAIT_S
        while time.monotonic() < fresh_deadline:
            cur = self.backend.latest_frame()
            if cur is not None and cur.ts > sent_at:
                return
            await asyncio.sleep(0.02)

    async def latency(self, samples: int = 10) -> dict:
        """Median press -> visible change, alternating right/left (home screen is a good bench)."""
        await self.require_ready()
        epoch = self.session_epoch
        results = []
        for i in range(samples):
            before = self.backend.latest_frame()
            if before is None:
                raise ConsoleError("no frames")
            base = await asyncio.to_thread(frames.small_gray, before.rgb)
            t0 = time.monotonic()
            await self.engine.run(
                [Step("press", button=Button.RIGHT if i % 2 == 0 else Button.LEFT, duration_ms=80)],
                lambda: self.check_input(epoch),
            )
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

    @routes.get("/status")
    async def status(request: web.Request) -> web.Response:
        return web.json_response(await daemon.status_dict())

    @routes.get("/wait_ready")
    async def wait_ready(request: web.Request) -> web.Response:
        timeout_s = max(0.0, min(float(request.query.get("timeout_s", 30)), 60.0))
        ready = await daemon.wait_ready(timeout_s)
        return web.json_response({"ready": ready, **(await daemon.status_dict())})

    @routes.get("/snapshot")
    async def snapshot(request: web.Request) -> web.Response:
        q = request.query
        return web.json_response(
            await daemon.snapshot(int(q.get("max_width", 1024)), int(q.get("quality", 80)))
        )

    @routes.get(FRAME_PATH)
    async def frame(request: web.Request) -> web.Response:
        """The newest frame as JPEG for the viewer window; 204 while it is still frame `after`.
        Ids restart with every session, so "different", not "greater"."""
        q = request.query
        cur = daemon.backend.latest_frame()
        if cur is None or cur.frame_id == int(q.get("after", 0)):
            return web.Response(status=204)
        jpeg, _, _ = await asyncio.to_thread(
            frames.to_jpeg, cur.rgb, int(q.get("max_width", 1920)), int(q.get("quality", 85))
        )
        return web.Response(body=jpeg, content_type="image/jpeg", headers={"X-Frame-Id": str(cur.frame_id)})

    @routes.post("/input")
    async def input_(request: web.Request) -> web.Response:
        body = await request.json()
        op = body.get("op")
        if op == "release_all":
            await daemon.pad.reset()
            return web.json_response({"ok": True})
        if op == "sequence":
            steps = [Step.parse(s) for s in body.get("steps", [])]
        elif op in ("press", "hold", "stick", "trigger"):
            steps = [Step.parse(body)]
        else:
            raise ValueError(f"unknown op {op!r}")
        await daemon.require_ready()
        before = daemon.backend.latest_frame()
        epoch = daemon.session_epoch

        def same_session() -> None:
            daemon.check_input(epoch)

        await daemon.engine.run(steps, same_session)
        result: dict = {"ok": True}
        ms = body.get("snapshot_after_ms")
        if ms is not None:
            sent_at = time.monotonic()
            wait_ms = max(0, min(int(ms), 10_000))
            await daemon.await_after_input(before, sent_at, wait_ms, bool(body.get("until_change")))
            result["snapshot"] = await daemon.snapshot(
                int(body.get("max_width", 1024)), int(body.get("quality", 80)), before, sent_at
            )
        return web.json_response(result)

    @routes.post("/wake")
    async def wake(request: web.Request) -> web.Response:
        await daemon.backend.wake()
        daemon.kick()
        return web.json_response({"ok": True})

    @routes.post("/standby")
    async def standby(request: web.Request) -> web.Response:
        daemon.pad.require_agent()
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
        # only agent calls keep the session alive: an open viewer window must not hold it forever
        touch = request.path != FRAME_PATH
        if touch:
            daemon.last_call = time.monotonic()
        try:
            return await handler(request)
        except (ValueError, KeyError) as err:
            return web.json_response({"ok": False, "error": f"bad request: {err}"}, status=400)
        except ConsoleError as err:
            return web.json_response({"ok": False, "error": str(err)}, status=409)
        except web.HTTPException:
            raise
        except Exception as err:  # the agent gets the reason, not a bare "Error executing tool"
            _LOG.exception("request %s failed", request.path)
            return web.json_response(
                {"ok": False, "error": f"daemon error: {type(err).__name__}: {err}"}, status=500
            )
        finally:
            if touch:
                daemon.last_call = time.monotonic()

    app = web.Application(middlewares=[errors])
    app.add_routes(routes)
    app[SHUTDOWN] = asyncio.Event()
    return app


def make_runner(app: web.Application) -> web.AppRunner:
    # handler_cancellation: a client that disconnects mid-hold cancels the handler,
    # and the engine's cleanup releases the input right away instead of at hold end.
    return web.AppRunner(app, handle_signals=False, handler_cancellation=True)


async def exit_when_idle(daemon: Daemon, stop: asyncio.Event) -> None:
    """Hand the console back when no agent has called for a while: the session would otherwise
    hold Remote Play forever, since the daemon reconnects on its own."""
    limit = daemon.cfg.idle_exit_s
    if limit <= 0:
        return
    while True:
        idle_s = time.monotonic() - daemon.last_call
        if idle_s >= limit and not daemon.pad.active:
            _LOG.info("no calls for %d s: closing the session and exiting", round(idle_s))
            stop.set()
            return
        await asyncio.sleep(max(0.1, min(limit - idle_s, 30)) + 0.1)


async def serve(cfg: Config, backend: ConsoleBackend | None = None) -> None:
    daemon = Daemon(cfg, backend)
    app = build_app(daemon)
    runner = make_runner(app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.daemon_host, cfg.daemon_port)
    await site.start()
    await daemon.start()
    _LOG.info("daemon listening on %s (backend %s)", cfg.daemon_url, cfg.backend)
    if cfg.viewer:
        from .viewer import spawn_viewer

        spawn_viewer()

    loop = asyncio.get_running_loop()
    stop = app[SHUTDOWN]
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    idle = asyncio.create_task(exit_when_idle(daemon, stop))
    try:
        await stop.wait()
    finally:
        idle.cancel()
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
