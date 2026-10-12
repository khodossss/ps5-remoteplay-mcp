"""Backend A: Remote Play via the vendored, patched pyremoteplay (protocol based on Chiaki)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque

from .. import console
from ..config import Config
from .base import Button, ConsoleError, ConsoleState, Frame, Side, Status

_LOG = logging.getLogger(__name__)
_STATUS_TTL_S = 3.0

# pyremoteplay names; Create is SHARE in its enum.
_BUTTON_NAMES = {b: ("SHARE" if b is Button.CREATE else b.value.upper()) for b in Button}


def _make_receiver():
    from ..vendor.pyremoteplay.receiver import QueueReceiver

    class VideoOnlyReceiver(QueueReceiver):
        """Keeps the newest decoded video frames with ids and timestamps; drops audio."""

        def __init__(self) -> None:
            super().__init__(max_frames=4)
            self.lock = threading.Lock()
            self.frame_id = 0
            self.frame_ts = 0.0
            self.arrivals: deque[float] = deque(maxlen=120)

        def _get_audio_codec(self, header: bytes) -> None:  # audio unused; skip decoder + resampler
            return None

        def handle_audio_data(self, buf: bytes) -> None:
            return None

        def handle_video(self, frame) -> None:  # called from the library's decoder thread
            now = time.monotonic()
            with self.lock:
                self.frame_id += 1
                self.frame_ts = now
                self.arrivals.append(now)
            super().handle_video(frame)

        def newest(self):
            with self.lock:
                return self.frame_id, self.frame_ts, self.get_latest_video_frame()

        def fps(self) -> float:
            with self.lock:
                recent = [t for t in self.arrivals if t > time.monotonic() - 2.0]
            return len(recent) / 2.0

    return VideoOnlyReceiver()


class RemotePlayBackend:
    def __init__(self, cfg: Config) -> None:
        console.prepare(cfg)
        self.cfg = cfg
        self.device = None  # created once the console's address is known
        self.receiver = None
        self._status_at = 0.0
        self._console = ConsoleState.UNKNOWN
        self._detail = ""
        self._rgb_cache: tuple[int, Frame] | None = None
        self._pressed: set[Button] = set()

    # --- helpers ---------------------------------------------------------
    async def _ensure_device(self, rediscover: bool = False):
        from ..vendor.pyremoteplay import RPDevice

        if self.device is None or rediscover:
            host = await console.resolve_host(self.cfg, rediscover=rediscover)
            if self.device is None or self.device.host != host:
                self.device = RPDevice(host)
        return self.device

    def _user(self) -> str:
        users = self.device.get_users()
        if self.cfg.user:
            if self.cfg.user not in users:
                raise ConsoleError(f"PSN user {self.cfg.user!r} is not linked to this console; run setup")
            return self.cfg.user
        if not users:
            raise ConsoleError("this computer is not linked to the console yet; run setup")
        return users[0]

    async def _refresh_console(self, force: bool = False) -> ConsoleState:
        if not force and time.monotonic() - self._status_at <= _STATUS_TTL_S:
            return self._console
        self._status_at = time.monotonic()
        if not console.is_set_up(self.cfg):
            self._console, self._detail = ConsoleState.NOT_SET_UP, "not set up yet: run the setup tool"
            return self._console
        try:
            device = await self._ensure_device()
            status = await device.async_get_status()
            if not status and not self.cfg.host:
                # the console may have a new address (DHCP): look for it again
                device = await self._ensure_device(rediscover=True)
                status = await device.async_get_status()
        except ConsoleError as err:
            self._console, self._detail = ConsoleState.UNREACHABLE, str(err)
            return self._console
        if not status:
            self._console, self._detail = ConsoleState.UNREACHABLE, f"no answer from {self.device.host}"
        else:
            self._console = ConsoleState.AWAKE if self.device.is_on else ConsoleState.STANDBY
            self._detail = ""
        return self._console

    @property
    def _session_up(self) -> bool:
        session = self.device.session if self.device else None
        return bool(session and session.is_ready and not session.is_stopped)

    def _controller(self):
        if not self._session_up:
            raise ConsoleError("no Remote Play session")
        return self.device.controller

    # --- ConsoleBackend --------------------------------------------------
    async def connect(self) -> None:
        if self._session_up:
            return
        state = await self._refresh_console(force=True)
        if state is not ConsoleState.AWAKE:
            raise ConsoleError(self._detail or f"console is {state.value}")
        self.device.disconnect()
        self.receiver = _make_receiver()
        session = self.device.create_session(
            self._user(),
            receiver=self.receiver,
            resolution=self.cfg.resolution,
            fps=self.cfg.fps,
            quality=self.cfg.quality,
            loop=asyncio.get_running_loop(),
        )
        if session is None:
            raise ConsoleError("could not create session (profile not found?)")
        if not await self.device.connect():
            raise ConsoleError(f"session refused: {getattr(session, 'error', '') or 'connect failed'}")
        if not await self.device.async_wait_for_session(timeout=20):
            self.device.disconnect()
            raise ConsoleError("session did not become ready")
        deadline = time.monotonic() + 15
        while self.receiver.frame_id == 0:
            if time.monotonic() > deadline or session.is_stopped:
                self.device.disconnect()
                raise ConsoleError("no video frames received")
            await asyncio.sleep(0.05)
        self.device.controller.start()
        self._pressed = set()

    async def disconnect(self) -> None:
        if self.device is None:
            return
        try:
            if self._session_up:
                await self.release_all()
        finally:
            self.device.controller.stop()
            self.device.disconnect()

    async def status(self) -> Status:
        try:
            state = await self._refresh_console()
        except OSError as err:
            return Status(ConsoleState.UNREACHABLE, False, detail=str(err))
        up = self._session_up
        return Status(
            console=state,
            session_up=up,
            fps=self.receiver.fps() if up and self.receiver else 0.0,
            last_frame_ts=self.receiver.frame_ts if up and self.receiver and self.receiver.frame_id else None,
            firmware=self.device.system_version if self.device else None,
            app=self.device.app_name if self.device else None,
            detail=self._detail,
        )

    def latest_frame(self) -> Frame | None:
        if self.receiver is None:
            return None
        frame_id, ts, av_frame = self.receiver.newest()
        if av_frame is None:
            return None
        if self._rgb_cache and self._rgb_cache[0] == frame_id:
            return self._rgb_cache[1]
        frame = Frame(av_frame.to_ndarray(format="rgb24"), frame_id, ts)
        self._rgb_cache = (frame_id, frame)
        return frame

    async def set_buttons(self, pressed: set[Button]) -> None:
        controller = self._controller()
        for button in self._pressed - pressed:
            controller.button(_BUTTON_NAMES[button], "release")
        for button in pressed - self._pressed:
            controller.button(_BUTTON_NAMES[button], "press")
        self._pressed = set(pressed)

    async def set_stick(self, side: Side, x: float, y: float) -> None:
        self._controller().stick(side.value, point=(float(x), float(y)))

    async def set_trigger(self, side: Side, value: float) -> None:
        from ..vendor.pyremoteplay.stream_packets import FeedbackEvent

        controller = self._controller()
        level = round(max(0.0, min(1.0, value)) * 255)

        class AnalogEvent(FeedbackEvent):
            @property
            def state(self) -> int:
                return level

        button = FeedbackEvent.Type.L2 if side is Side.LEFT else FeedbackEvent.Type.R2
        controller._add_event_buffer(AnalogEvent(button, is_active=level > 0))
        controller._send_event()

    async def release_all(self) -> None:
        if not self._session_up:
            self._pressed = set()
            return
        controller = self.device.controller
        for button in Button:
            controller.button(_BUTTON_NAMES[button], "release")
        self._pressed = set()
        for side in Side:
            controller.stick(side.value, point=(0.0, 0.0))
            await self.set_trigger(side, 0.0)

    async def wake(self) -> None:
        state = await self._refresh_console(force=True)
        if state is ConsoleState.AWAKE:
            return
        if state is not ConsoleState.STANDBY:
            raise ConsoleError(self._detail or f"console is {state.value}")
        self.device.wakeup(user=self._user())
        if not await self.device.async_wait_for_wakeup(timeout=60):
            raise ConsoleError(
                "console did not wake within 60 s (is 'Enable Turning On PS5 from Network' on?)"
            )
        self._status_at = 0.0

    async def standby(self) -> None:
        if self._session_up:
            await self.release_all()
            ok = await self.device.session.async_standby()
        else:
            state = await self._refresh_console(force=True)
            if state is not ConsoleState.AWAKE:
                raise ConsoleError(self._detail or f"console is {state.value}")
            ok = await self.device.standby(self._user())
        self.device.controller.stop()
        self.device.disconnect()
        self._status_at = 0.0
        if not ok:
            raise ConsoleError("standby request was not acknowledged")
