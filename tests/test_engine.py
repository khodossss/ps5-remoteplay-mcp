import asyncio
import time

import pytest

from ps5agent.backend import Button, Side
from ps5agent.backend.fake import FakeBackend
from ps5agent.engine import InputEngine, Step


@pytest.fixture
async def engine(fake: FakeBackend):
    await fake.connect()
    e = InputEngine(fake, max_hold_ms=1_000)
    e.start()
    yield e
    await e.stop()


async def test_press_presses_then_releases(engine, fake):
    await engine.press(Button.CROSS, 20)
    ops = [(op, arg) for _, op, arg in fake.log]
    assert ops == [("buttons", ["cross"]), ("buttons", [])]
    assert fake.idle


async def test_right_moves_fake_focus(engine, fake):
    await engine.press(Button.RIGHT, 10)
    assert fake.index == 1


async def test_hold_longer_than_cap_is_rejected(engine, fake):
    with pytest.raises(ValueError):
        await engine.hold(Button.R1, 5_000)
    assert fake.log == []


async def test_sequence_runs_in_order(engine, fake):
    steps = [
        Step.parse(s)
        for s in [
            {"op": "press", "button": "down", "duration_ms": 5},
            {"op": "wait", "duration_ms": 5},
            {"op": "stick", "side": "left", "x": 0.5, "y": -1, "duration_ms": 5},
            {"op": "trigger", "side": "right", "value": 0.7, "duration_ms": 5},
        ]
    ]
    await engine.run(steps)
    ops = [(op, arg) for _, op, arg in fake.log]
    assert ops == [
        ("buttons", ["down"]),
        ("buttons", []),
        ("stick", ("left", 0.5, -1.0)),
        ("stick", ("left", 0.0, 0.0)),
        ("trigger", ("right", 0.7)),
        ("trigger", ("right", 0.0)),
    ]
    assert fake.idle


async def test_backend_error_mid_hold_releases_everything(engine, fake):
    async def explode_later():
        await asyncio.sleep(0.02)
        fake.fail_next = RuntimeError("session lost")

    bomb = asyncio.create_task(explode_later())
    with pytest.raises(RuntimeError):
        await engine.run(
            [
                Step.parse({"op": "hold", "button": "r2", "duration_ms": 50}),
                Step.parse({"op": "press", "button": "cross"}),
            ]
        )
    await bomb
    assert fake.log[-1][1] == "release_all"
    assert fake.idle


async def test_cancelled_request_releases(engine, fake):
    task = asyncio.create_task(engine.hold(Button.L2, 900))
    await asyncio.sleep(0.05)
    assert Button.L2 in fake.pressed
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.idle


async def test_release_all_works_during_hold(engine, fake):
    task = asyncio.create_task(engine.stick(Side.RIGHT, 1.0, 0.0, 900))
    await asyncio.sleep(0.05)
    assert fake.sticks[Side.RIGHT] == (1.0, 0.0)
    await engine.release_all()
    assert fake.idle
    await task


async def test_watchdog_releases_stuck_input(fake):
    await fake.connect()
    engine = InputEngine(fake, max_hold_ms=100)
    engine.start()
    await fake.set_buttons({Button.R2})  # simulate input stuck outside the engine's own cleanup
    engine._active_since = time.monotonic()
    await asyncio.sleep(1.0)
    assert engine.watchdog_trips >= 1
    assert fake.idle
    await engine.stop()


async def test_inputs_are_serialized(engine, fake):
    await asyncio.gather(engine.press(Button.CROSS, 30), engine.press(Button.CIRCLE, 30))
    pressed_sets = [arg for _, op, arg in fake.log if op == "buttons"]
    assert all(len(s) <= 1 for s in pressed_sets)  # never both at once


@pytest.mark.parametrize(
    "raw",
    [
        {"op": "jump"},
        {"op": "press", "button": "start"},
        {"op": "stick", "side": "left", "x": 2},
        {"op": "trigger", "side": "left", "value": -0.1},
        {"op": "press", "button": "cross", "duration_ms": -5},
    ],
)
def test_step_validation(raw):
    with pytest.raises(ValueError):
        Step.parse(raw)
