"""Human priority and release lifecycle (graph `ps5-agent-bridge`, nodes #22, #24)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from .backend import ConsoleError, Side
from .gamepad import IDLE, PadState

if TYPE_CHECKING:
    from .daemon import Daemon

_LOG = logging.getLogger(__name__)
READER_TIMEOUT_S = 0.5
HUMAN_GRACE_S = 2.0


class PadBridge:
    def __init__(self, daemon: Daemon) -> None:
        self.daemon = daemon
        self._queue: asyncio.Queue[tuple[int, PadState, float]] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._latest = IDLE
        self._applied = IDLE
        self._generation = 0
        self._last_seen = 0.0
        self._until = 0.0
        self._rearm = False
        self._reserved = False

    @property
    def active(self) -> bool:
        return self._reserved or time.monotonic() < self._until

    def require_agent(self) -> None:
        if self.active:
            raise ConsoleError("input not sent: the user is controlling the PS5 with the computer gamepad")

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def submit(self, state: PadState, sampled_at: float | None = None) -> None:
        if self._task is None or self._task.done():
            return
        sampled_at = time.monotonic() if sampled_at is None else sampled_at
        if time.monotonic() - sampled_at > READER_TIMEOUT_S:
            return
        self._last_seen = sampled_at
        if self.daemon.state != "up" or self._rearm:
            self._rearm = not state.idle
            self._latest = state
            return
        if not state.idle:
            self._reserved = True
            self._until = self._last_seen + HUMAN_GRACE_S
            self.daemon.last_call = self._last_seen
        if state != self._latest:
            self._latest = state
            self._queue.put_nowait((self._generation, state, self._last_seen))

    async def reset(self) -> None:
        owned_input = self._reserved or not self._applied.idle
        self._generation += 1
        self._rearm = not self._latest.idle
        self._latest = self._applied = IDLE
        self._reserved = False
        self._until = 0.0
        while not self._queue.empty():
            self._queue.get_nowait()
        if owned_input:
            await self.daemon.engine.preempt()
            async with self.daemon.engine.manual():
                await self.daemon.engine.release_all()
        else:
            await self.daemon.engine.release_all()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.reset()

    async def _run(self) -> None:
        while True:
            try:
                generation, state, sampled_at = await asyncio.wait_for(self._queue.get(), READER_TIMEOUT_S)
            except TimeoutError:
                if self._reserved and time.monotonic() - self._last_seen > READER_TIMEOUT_S:
                    _LOG.warning("gamepad reader stopped reporting: releasing input")
                    await self.reset()
                continue
            if generation != self._generation or self.daemon.state != "up":
                continue
            try:
                if self._applied.idle and not state.idle:
                    await self.daemon.engine.preempt()
                async with self.daemon.engine.manual():
                    if generation != self._generation or self.daemon.state != "up":
                        continue
                    if time.monotonic() - sampled_at > READER_TIMEOUT_S:
                        raise ConsoleError("gamepad sample expired before forwarding")
                    await self._apply(state)
                    if generation != self._generation:
                        continue
                    self._applied = state
                if state.idle:
                    self._reserved = not self._latest.idle
                    self._until = time.monotonic() + HUMAN_GRACE_S
                    self.daemon.last_call = time.monotonic()
            except Exception:
                _LOG.exception("gamepad forwarding failed")
                await self.reset()

    async def _apply(self, state: PadState) -> None:
        backend = self.daemon.backend
        if state.buttons != self._applied.buttons:
            await backend.set_buttons(set(state.buttons))
        for side in Side:
            if state.sticks[side] != self._applied.sticks[side]:
                await backend.set_stick(side, *state.sticks[side])
            if state.triggers[side] != self._applied.triggers[side]:
                await backend.set_trigger(side, state.triggers[side])
