"""Gamepad attached to this computer (DualSense over USB or Bluetooth), read through SDL2.

SDL's GameController API names buttons the same way for every connection type, so one mapping
covers USB and Bluetooth (graph `ps5-agent-bridge`, node #20).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .backend import Button, Side

STICK_DEADZONE = 0.08
AXIS_MAX = 32767.0


@dataclass(frozen=True)
class PadState:
    buttons: frozenset[Button] = frozenset()
    sticks: dict[Side, tuple[float, float]] = field(
        default_factory=lambda: {Side.LEFT: (0.0, 0.0), Side.RIGHT: (0.0, 0.0)}
    )
    triggers: dict[Side, float] = field(default_factory=lambda: {Side.LEFT: 0.0, Side.RIGHT: 0.0})

    @property
    def idle(self) -> bool:
        return (
            not self.buttons
            and all(v == (0.0, 0.0) for v in self.sticks.values())
            and not any(self.triggers.values())
        )


IDLE = PadState()


def _axis(raw: int) -> float:
    value = max(-1.0, min(1.0, raw / AXIS_MAX))
    return 0.0 if abs(value) < STICK_DEADZONE else round(value, 2)


def _trigger(raw: int) -> float:
    value = max(0.0, min(1.0, raw / AXIS_MAX))
    return 0.0 if value < STICK_DEADZONE else round(value, 2)


def state_from_sdl(pressed: set[str], axes: dict[str, int]) -> PadState:
    """`pressed`: SDL button names without the SDL_CONTROLLER_BUTTON_ prefix; `axes`: raw SDL axis values."""
    buttons = frozenset(BUTTONS[name] for name in pressed if name in BUTTONS)
    return PadState(
        buttons,
        {
            Side.LEFT: (_axis(axes.get("LEFTX", 0)), _axis(axes.get("LEFTY", 0))),
            Side.RIGHT: (_axis(axes.get("RIGHTX", 0)), _axis(axes.get("RIGHTY", 0))),
        },
        {Side.LEFT: _trigger(axes.get("TRIGGERLEFT", 0)), Side.RIGHT: _trigger(axes.get("TRIGGERRIGHT", 0))},
    )


# SDL names follow the Xbox layout by position: A is the bottom face button (cross on a DualSense).
BUTTONS = {
    "A": Button.CROSS,
    "B": Button.CIRCLE,
    "X": Button.SQUARE,
    "Y": Button.TRIANGLE,
    "BACK": Button.CREATE,
    "GUIDE": Button.PS,
    "START": Button.OPTIONS,
    "LEFTSTICK": Button.L3,
    "RIGHTSTICK": Button.R3,
    "LEFTSHOULDER": Button.L1,
    "RIGHTSHOULDER": Button.R1,
    "DPAD_UP": Button.UP,
    "DPAD_DOWN": Button.DOWN,
    "DPAD_LEFT": Button.LEFT,
    "DPAD_RIGHT": Button.RIGHT,
    "TOUCHPAD": Button.TOUCHPAD,
}
AXES = ("LEFTX", "LEFTY", "RIGHTX", "RIGHTY", "TRIGGERLEFT", "TRIGGERRIGHT")
