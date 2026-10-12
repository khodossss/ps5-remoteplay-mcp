"""Backend contract: everything that depends on how we reach the console."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import numpy as np


class Button(StrEnum):
    CROSS = "cross"
    CIRCLE = "circle"
    SQUARE = "square"
    TRIANGLE = "triangle"
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    L1 = "l1"
    R1 = "r1"
    L2 = "l2"
    R2 = "r2"
    L3 = "l3"
    R3 = "r3"
    OPTIONS = "options"
    CREATE = "create"
    TOUCHPAD = "touchpad"
    PS = "ps"


class Side(StrEnum):
    LEFT = "left"
    RIGHT = "right"


class ConsoleState(StrEnum):
    AWAKE = "awake"
    STANDBY = "standby"
    UNREACHABLE = "unreachable"
    NOT_SET_UP = "not_set_up"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Frame:
    rgb: np.ndarray  # H x W x 3, uint8
    frame_id: int
    ts: float  # time.monotonic() when the frame was decoded


@dataclass(frozen=True)
class Status:
    console: ConsoleState
    session_up: bool
    fps: float = 0.0
    last_frame_ts: float | None = None
    firmware: str | None = None
    app: str | None = None
    detail: str = ""


class ConsoleError(RuntimeError):
    """The console or session refused or failed an operation."""


class ConsoleBackend(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def status(self) -> Status: ...
    def latest_frame(self) -> Frame | None: ...
    async def set_buttons(self, pressed: set[Button]) -> None: ...
    async def set_stick(self, side: Side, x: float, y: float) -> None: ...  # -1..1, y=-1 is up
    async def set_trigger(self, side: Side, value: float) -> None: ...  # 0..1
    async def release_all(self) -> None: ...
    async def wake(self) -> None: ...
    async def standby(self) -> None: ...
