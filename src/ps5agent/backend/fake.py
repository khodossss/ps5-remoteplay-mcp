"""Fake console for tests and demos: frames from a PNG folder (or synthetic), input is logged.

`right` / `left` presses step to the next / previous frame, like moving focus on the home screen.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .base import Button, ConsoleError, ConsoleState, Frame, Side, Status

SYNTHETIC_FRAMES = 5


def _synthetic(index: int, count: int, size: tuple[int, int] = (1280, 720)) -> np.ndarray:
    img = Image.new("RGB", size, (24, 26, 40))
    draw = ImageDraw.Draw(img)
    tile_w = size[0] // (count + 1)
    for i in range(count):
        x0 = tile_w // 2 + i * tile_w
        box = (x0 + 10, 260, x0 + tile_w - 10, 460)
        draw.rectangle(box, fill=(70, 70, 90), outline=(255, 255, 255) if i == index else None, width=6)
        draw.text((x0 + 30, 340), f"TILE {i}", fill=(230, 230, 230))
    draw.text((40, 40), f"FAKE PS5 - focus {index}", fill=(255, 255, 255))
    return np.asarray(img, dtype=np.uint8)


class FakeBackend:
    def __init__(self, frames_dir: str | None = None) -> None:
        if frames_dir:
            paths = sorted(Path(frames_dir).glob("*.png"))
            if not paths:
                raise ValueError(f"no PNG frames in {frames_dir}")
            self._frames = [np.asarray(Image.open(p).convert("RGB"), dtype=np.uint8) for p in paths]
        else:
            self._frames = [_synthetic(i, SYNTHETIC_FRAMES) for i in range(SYNTHETIC_FRAMES)]
        self.index = 0
        self.frame_id = 1
        self.connected = False
        self.awake = True
        self.pressed: set[Button] = set()
        self.sticks: dict[Side, tuple[float, float]] = {Side.LEFT: (0.0, 0.0), Side.RIGHT: (0.0, 0.0)}
        self.triggers: dict[Side, float] = {Side.LEFT: 0.0, Side.RIGHT: 0.0}
        self.log: list[tuple[float, str, object]] = []
        self.fail_next: Exception | None = None  # injected failure for tests
        self.stalled = False  # session up but no new frames, like a frozen Remote Play stream

    def _record(self, op: str, arg: object) -> None:
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            raise err
        self.log.append((time.monotonic(), op, arg))

    async def connect(self) -> None:
        if not self.awake:
            raise ConsoleError("console is in standby")
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def status(self) -> Status:
        return Status(
            console=ConsoleState.AWAKE if self.awake else ConsoleState.STANDBY,
            session_up=self.connected,
            fps=30.0 if self.connected else 0.0,
            last_frame_ts=self._frame_ts() if self.connected else None,
            firmware="fake",
        )

    def latest_frame(self) -> Frame | None:
        if not self.connected:
            return None
        return Frame(self._frames[self.index], self.frame_id, self._frame_ts())

    def _frame_ts(self) -> float:
        return time.monotonic() - (60.0 if self.stalled else 0.0)

    async def set_buttons(self, pressed: set[Button]) -> None:
        newly = pressed - self.pressed
        self._record("buttons", sorted(pressed))
        for button in newly:
            step = {Button.RIGHT: 1, Button.LEFT: -1}.get(button, 0)
            if step:
                self.index = (self.index + step) % len(self._frames)
                self.frame_id += 1
        self.pressed = set(pressed)

    async def set_stick(self, side: Side, x: float, y: float) -> None:
        self._record("stick", (side.value, x, y))
        self.sticks[side] = (x, y)

    async def set_trigger(self, side: Side, value: float) -> None:
        self._record("trigger", (side.value, value))
        self.triggers[side] = value

    async def release_all(self) -> None:
        self.log.append((time.monotonic(), "release_all", None))
        self.pressed = set()
        self.sticks = {Side.LEFT: (0.0, 0.0), Side.RIGHT: (0.0, 0.0)}
        self.triggers = {Side.LEFT: 0.0, Side.RIGHT: 0.0}

    async def wake(self) -> None:
        self.awake = True

    async def standby(self) -> None:
        self.awake = False
        self.connected = False

    @property
    def idle(self) -> bool:
        """True when nothing is held — the invariant the daemon must restore after any failure."""
        centered = all(v == (0.0, 0.0) for v in self.sticks.values())
        return not self.pressed and centered and not any(self.triggers.values())
