import asyncio

import pytest

from ps5agent.backend import Button, ConsoleError, Side
from ps5agent.engine import InputAbortedError, InputEngine, Step


async def test_preempt_drops_running_and_queued_sequences(fake):
    engine = InputEngine(fake)
    running = asyncio.create_task(engine.hold(Button.CROSS, 5_000))
    await asyncio.sleep(0.01)
    queued = asyncio.create_task(engine.press(Button.RIGHT, 20))
    await asyncio.sleep(0)
    await asyncio.wait_for(engine.preempt(), 0.5)
    with pytest.raises(InputAbortedError, match="preempted"):
        await running
    with pytest.raises(ConsoleError, match="preempted"):
        await queued
    assert fake.index == 0 and fake.idle
    await engine.press(Button.RIGHT, 1)
    assert fake.index == 1 and fake.idle


@pytest.mark.parametrize("op", ["hold", "stick", "trigger", "wait"])
async def test_preempt_interrupts_every_input_kind(fake, op):
    engine = InputEngine(fake)
    step = Step(op, button=Button.CROSS, side=Side.LEFT, x=1, value=1, duration_ms=5_000)
    task = asyncio.create_task(engine.run([step, Step("press", button=Button.RIGHT)]))
    await asyncio.sleep(0.01)
    await asyncio.wait_for(engine.preempt(), 0.5)
    with pytest.raises(InputAbortedError, match="after 0 of 2"):
        await task
    assert fake.index == 0 and fake.idle
