import asyncio
import time

import pytest
from mcp import Client
from mcp.types import TextContent

from ps5agent.backend import Button, Side
from ps5agent.client import DaemonClient, DaemonError
from ps5agent.daemon import exit_when_idle
from ps5agent.gamepad import IDLE, PadState, state_from_sdl
from ps5agent.mcp_server import build_server


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():  # noqa: ASYNC110 — observing backend state, not a notification
            await asyncio.sleep(0.005)


def test_dualsense_mapping_and_axis_limits():
    state = state_from_sdl({"A", "TOUCHPAD", "BACK"}, {"LEFTX": -32768, "TRIGGERRIGHT": 32767})
    assert state.buttons == {Button.CROSS, Button.TOUCHPAD, Button.CREATE}
    assert state.sticks[Side.LEFT] == (-1.0, 0.0)
    assert state.triggers[Side.RIGHT] == 1.0
    assert state_from_sdl(set(), {"LEFTX": 100, "TRIGGERRIGHT": -32768}).idle


async def test_human_takes_over_agent_and_mcp_refuses_input(cfg, daemon, fake):
    client = DaemonClient(cfg, autostart=False)
    try:
        async with Client(build_server(cfg, client)) as mcp:
            task = asyncio.create_task(client.input("hold", button="cross", duration_ms=1_500))
            await until(lambda: Button.CROSS in fake.pressed)
            human = PadState(frozenset({Button.CIRCLE}))
            daemon.pad.submit(human)
            await until(lambda: fake.pressed == {Button.CIRCLE})
            with pytest.raises(DaemonError, match="preempted"):
                await task
            result = await mcp.call_tool("press", {"button": "right"})
            assert result.is_error
            assert "computer gamepad" in " ".join(
                c.text for c in result.content if isinstance(c, TextContent)
            )
            status = await client.status()
            assert status["human_input"] is True
            assert fake.pressed == {Button.CIRCLE} and fake.index == 0
            daemon.pad.submit(IDLE)
            await until(lambda: fake.idle)
            assert daemon.pad.active
            async with asyncio.timeout(3):
                while daemon.pad.active:  # noqa: ASYNC110 — exercise the real grace deadline
                    await asyncio.sleep(0.02)
            await client.input("press", button="right", duration_ms=1)
            assert fake.index == 1 and fake.idle
    finally:
        await client.aclose()


async def test_axes_unplug_and_reader_failure_release(daemon, fake):
    state = state_from_sdl(set(), {"LEFTX": 32767, "TRIGGERRIGHT": 32767})
    daemon.pad.submit(state)
    await until(lambda: fake.triggers[Side.RIGHT] == 1)
    daemon.pad.submit(IDLE)
    await until(lambda: fake.idle)
    daemon.pad.submit(state)
    await until(lambda: fake.triggers[Side.RIGHT] == 1)
    await until(lambda: fake.idle)  # no heartbeat: reader death clears the held axes
    daemon.pad.submit(state)  # held state must not be replayed after failure
    await asyncio.sleep(0.02)
    assert fake.idle
    daemon.pad.submit(IDLE)
    assert fake.idle


async def test_session_loss_drops_old_pad_state(daemon, fake):
    state = PadState(frozenset({Button.CIRCLE}))
    daemon.pad.submit(state)
    await until(lambda: fake.pressed == {Button.CIRCLE})
    fake.connected = False
    await until(lambda: daemon.session_epoch == 1 and fake.connected)
    daemon.pad.submit(state)
    await asyncio.sleep(0.02)
    assert fake.idle
    daemon.pad.submit(IDLE)
    daemon.pad.submit(state)
    await until(lambda: fake.pressed == {Button.CIRCLE})
    await daemon.stop()
    assert fake.idle


async def test_forwarding_error_releases_and_requires_neutral(daemon, fake):
    fake.fail_next = RuntimeError("lost stream")
    state = PadState(frozenset({Button.CIRCLE}))
    daemon.pad.submit(state)
    await until(lambda: daemon.pad._rearm)
    assert fake.idle
    daemon.pad.submit(state)
    await asyncio.sleep(0.02)
    assert fake.idle
    daemon.pad.submit(IDLE)
    daemon.pad.submit(state)
    await until(lambda: fake.pressed == {Button.CIRCLE})
    await daemon.pad.reset()
    assert fake.idle


async def test_healthy_held_pad_is_not_capped_by_agent_watchdog(daemon, fake):
    daemon.engine.max_hold_ms = 10
    state = PadState(frozenset({Button.CIRCLE}))
    for _ in range(7):
        daemon.pad.submit(state)
        await asyncio.sleep(0.1)
    assert fake.pressed == {Button.CIRCLE} and daemon.engine.watchdog_trips == 0
    assert daemon.last_call > time.monotonic() - 0.2
    await daemon.pad.stop()
    assert fake.idle


async def test_stalled_stream_releases_human_input(daemon, fake):
    state = PadState(frozenset({Button.CIRCLE}))
    daemon.pad.submit(state)
    await until(lambda: fake.pressed == {Button.CIRCLE})
    fake.stall_s = 5
    for _ in range(7):
        daemon.pad.submit(state)
        await asyncio.sleep(0.1)
    assert daemon.state == "stalled" and fake.idle
    fake.stall_s = 0
    await until(lambda: daemon.state == "up")
    daemon.pad.submit(state)
    assert fake.idle


async def test_human_activity_keeps_idle_daemon_alive(cfg, daemon, fake):
    from dataclasses import replace

    daemon.cfg = replace(cfg, idle_exit_s=1)
    stop = asyncio.Event()
    task = asyncio.create_task(exit_when_idle(daemon, stop))
    try:
        state = PadState(frozenset({Button.CIRCLE}))
        for _ in range(13):
            daemon.pad.submit(state)
            await asyncio.sleep(0.1)
        assert not stop.is_set()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await daemon.pad.reset()
    assert fake.idle


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
