import asyncio
import time

from ps5agent.backend import Button
from ps5agent.gamepad import PadState

from .conftest import wait_until as until


async def test_stale_reader_callback_never_replays_input(daemon, fake):
    state = PadState(frozenset({Button.CIRCLE}))
    daemon.pad.submit(state, time.monotonic() - 2)
    await asyncio.sleep(0.02)
    assert fake.idle and not daemon.pad.active


async def test_pad_sample_expiring_while_waiting_for_cleanup_is_dropped(daemon, fake):
    async with daemon.engine.manual():
        daemon.pad.submit(PadState(frozenset({Button.CIRCLE})))
        await asyncio.sleep(0.6)
    await until(lambda: not daemon.pad.active)
    assert fake.idle
    assert not any(op == "buttons" and arg for _, op, arg in fake.log)
