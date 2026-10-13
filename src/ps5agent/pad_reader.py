"""SDL reader for the computer gamepad (graph `ps5-agent-bridge`, node #20)."""

from __future__ import annotations

import logging
import threading
import warnings
from collections.abc import Callable

from .gamepad import AXES, BUTTONS, IDLE, PadState, state_from_sdl

_LOG = logging.getLogger(__name__)
POLL_S = 1 / 120


class PadReader:
    """Polls the first game controller on its own thread (SDL wants every call on one thread) and
    reports its state as a heartbeat; IDLE when unplugged. `name` is the open controller."""

    def __init__(self, on_state: Callable[[PadState], None]) -> None:
        self.on_state = on_state
        self.name: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """False when SDL is not available here; the bridge then works without a local gamepad."""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # pysdl2 warns which SDL binary it picked
                import sdl2  # noqa: F401
        except Exception as err:  # missing wheel or shared library on this platform
            _LOG.warning("gamepad on this computer disabled: SDL2 unavailable (%s)", err)
            return False
        self._thread = threading.Thread(target=self._run, name="gamepad", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        import sdl2

        # the daemon has no window: without this hint SDL only reports input to a focused window
        sdl2.SDL_SetHint(sdl2.SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, b"1")
        if sdl2.SDL_Init(sdl2.SDL_INIT_GAMECONTROLLER) != 0:
            _LOG.warning(
                "gamepad on this computer disabled: %s", sdl2.SDL_GetError().decode(errors="replace")
            )
            return
        buttons = {name: getattr(sdl2, f"SDL_CONTROLLER_BUTTON_{name}") for name in BUTTONS}
        axes = {name: getattr(sdl2, f"SDL_CONTROLLER_AXIS_{name}") for name in AXES}
        controller = None
        try:
            while not self._stop.is_set():
                sdl2.SDL_PumpEvents()
                if controller is not None and not sdl2.SDL_GameControllerGetAttached(controller):
                    _LOG.info("gamepad disconnected: %s", self.name)
                    sdl2.SDL_GameControllerClose(controller)
                    controller, self.name = None, None
                if controller is None:
                    controller = self._open(sdl2)
                state = IDLE
                if controller is not None:
                    pressed = {
                        n for n, b in buttons.items() if sdl2.SDL_GameControllerGetButton(controller, b)
                    }
                    raw = {n: sdl2.SDL_GameControllerGetAxis(controller, a) for n, a in axes.items()}
                    state = state_from_sdl(pressed, raw)
                self.on_state(state)
                self._stop.wait(POLL_S if controller is not None else 1.0)
        except Exception:
            _LOG.exception("gamepad reader failed")
        finally:
            if controller is not None:
                sdl2.SDL_GameControllerClose(controller)
            sdl2.SDL_QuitSubSystem(sdl2.SDL_INIT_GAMECONTROLLER)
            self.name = None
            self.on_state(IDLE)

    def _open(self, sdl2):
        for index in range(sdl2.SDL_NumJoysticks()):
            if sdl2.SDL_IsGameController(index):
                controller = sdl2.SDL_GameControllerOpen(index)
                if controller:
                    self.name = (sdl2.SDL_GameControllerName(controller) or b"gamepad").decode(
                        errors="replace"
                    )
                    _LOG.info("gamepad connected: %s", self.name)
                    return controller
        return None
