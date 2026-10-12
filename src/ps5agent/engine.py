"""Input engine: presses, holds and sequences built on backend primitives.

Invariant: whatever happens — exception, timeout, cancellation (client gone), watchdog —
no button stays pressed and no stick/trigger stays deflected.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from .backend import Button, ConsoleBackend, Side

_LOG = logging.getLogger(__name__)
MAX_SEQUENCE_MS = 60_000
MAX_SEQUENCE_STEPS = 100


@dataclass(frozen=True)
class Step:
    op: str  # press | hold | stick | trigger | wait
    button: Button | None = None
    side: Side | None = None
    x: float = 0.0
    y: float = 0.0
    value: float = 0.0
    duration_ms: int = 80

    @classmethod
    def parse(cls, raw: dict) -> Step:
        op = raw.get("op") or raw.get("type")
        if op not in ("press", "hold", "stick", "trigger", "wait"):
            raise ValueError(f"unknown step op {op!r}; use press, hold, stick, trigger or wait")
        default_ms = 80 if op == "press" else 0
        step = cls(
            op=op,
            button=Button(str(raw["button"]).lower()) if op in ("press", "hold") else None,
            side=Side(str(raw["side"]).lower()) if op in ("stick", "trigger") else None,
            x=float(raw.get("x", 0.0)),
            y=float(raw.get("y", 0.0)),
            value=float(raw.get("value", 0.0)),
            duration_ms=int(raw.get("duration_ms", raw.get("ms", default_ms))),
        )
        if step.duration_ms < 0:
            raise ValueError("duration_ms must be >= 0")
        if not (-1.0 <= step.x <= 1.0 and -1.0 <= step.y <= 1.0):
            raise ValueError("stick x and y must be within -1..1")
        if not 0.0 <= step.value <= 1.0:
            raise ValueError("trigger value must be within 0..1")
        return step


class InputEngine:
    def __init__(self, backend: ConsoleBackend, max_hold_ms: int = 10_000) -> None:
        self.backend = backend
        self.max_hold_ms = max_hold_ms
        self._lock = asyncio.Lock()
        self._pressed: set[Button] = set()
        self._active_since: float | None = None
        self._watchdog: asyncio.Task | None = None
        self.watchdog_trips = 0

    # --- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._watchdog is None:
            self._watchdog = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        if self._watchdog:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None
        await self.release_all()

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            since = self._active_since
            if since is not None and (time.monotonic() - since) * 1000 > self.max_hold_ms + 500:
                _LOG.warning("watchdog: input held past %d ms, releasing everything", self.max_hold_ms)
                self.watchdog_trips += 1
                await self._release()

    # --- primitives ------------------------------------------------------
    async def _release(self) -> None:
        self._pressed = set()
        self._active_since = None
        try:
            await self.backend.release_all()
        except Exception:
            _LOG.exception("release_all failed")

    @contextlib.asynccontextmanager
    async def _active(self) -> AsyncIterator[None]:
        """Serialize input; on any exit path that is not clean, release everything."""
        async with self._lock:
            self._active_since = time.monotonic()
            clean = False
            try:
                yield
                clean = True
            finally:
                self._active_since = None
                if not clean:
                    # shield: a cancelled request must still get its buttons released
                    await asyncio.shield(self._release())

    def _check_duration(self, duration_ms: int) -> None:
        if not 0 <= duration_ms <= self.max_hold_ms:
            raise ValueError(f"duration_ms must be within 0..{self.max_hold_ms}")

    async def _do(self, step: Step) -> None:
        if step.op == "wait":
            await asyncio.sleep(step.duration_ms / 1000)
        elif step.op in ("press", "hold"):
            self._check_duration(step.duration_ms)
            self._pressed = self._pressed | {step.button}
            await self.backend.set_buttons(self._pressed)
            try:
                await asyncio.sleep(step.duration_ms / 1000)
            finally:
                self._pressed = self._pressed - {step.button}
                await self.backend.set_buttons(self._pressed)
        elif step.op == "stick":
            self._check_duration(step.duration_ms)
            await self.backend.set_stick(step.side, step.x, step.y)
            try:
                await asyncio.sleep(step.duration_ms / 1000)
            finally:
                await self.backend.set_stick(step.side, 0.0, 0.0)
        elif step.op == "trigger":
            self._check_duration(step.duration_ms)
            await self.backend.set_trigger(step.side, step.value)
            try:
                await asyncio.sleep(step.duration_ms / 1000)
            finally:
                await self.backend.set_trigger(step.side, 0.0)

    # --- public ops ------------------------------------------------------
    async def press(self, button: Button, duration_ms: int = 80) -> None:
        await self.run([Step("press", button=button, duration_ms=duration_ms)])

    async def hold(self, button: Button, duration_ms: int) -> None:
        await self.run([Step("hold", button=button, duration_ms=duration_ms)])

    async def stick(self, side: Side, x: float, y: float, duration_ms: int) -> None:
        await self.run(
            [Step.parse({"op": "stick", "side": side, "x": x, "y": y, "duration_ms": duration_ms})]
        )

    async def trigger(self, side: Side, value: float, duration_ms: int) -> None:
        await self.run(
            [Step.parse({"op": "trigger", "side": side, "value": value, "duration_ms": duration_ms})]
        )

    async def run(self, steps: list[Step]) -> None:
        if len(steps) > MAX_SEQUENCE_STEPS:
            raise ValueError(f"at most {MAX_SEQUENCE_STEPS} steps per sequence")
        if sum(s.duration_ms for s in steps) > MAX_SEQUENCE_MS:
            raise ValueError(f"a sequence may last at most {MAX_SEQUENCE_MS} ms")
        for step in steps:
            if step.op != "wait":
                self._check_duration(step.duration_ms)
        async with self._active():
            for step in steps:
                await self._do(step)

    async def release_all(self) -> None:
        # Not behind the lock on purpose: must work while a hold is in progress.
        await self._release()
