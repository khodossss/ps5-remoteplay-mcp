"""Field-session failures: long sequences, dead streams, stale snapshots, handing the console back."""

import asyncio
import threading
import time

import pytest
from mcp import Client
from mcp.types import TextContent

from ps5agent.client import DaemonClient, DaemonError
from ps5agent.config import from_env
from ps5agent.daemon import Daemon, exit_when_idle
from ps5agent.engine import InputAbortedError, InputEngine, Step
from ps5agent.mcp_server import build_server
from ps5agent.vendor.pyremoteplay.stream import RPStream

from .conftest import free_port


@pytest.fixture
async def client(cfg, daemon):
    c = DaemonClient(cfg, autostart=False)
    yield c
    await c.aclose()


def _text(result) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


# --- engine --------------------------------------------------------------
async def test_long_sequence_does_not_trip_watchdog(fake):
    await fake.connect()
    engine = InputEngine(fake, max_hold_ms=300)
    engine.start()
    steps = [
        Step.parse({"op": "press", "button": "right"}),
        Step.parse({"op": "wait", "duration_ms": 1_200}),
    ] * 2
    await engine.run(steps)
    await engine.stop()
    assert engine.watchdog_trips == 0
    assert fake.index == 2 and fake.idle


async def test_failed_step_reports_progress_and_drops_the_rest(fake):
    await fake.connect()
    engine = InputEngine(fake, max_hold_ms=1_000)
    press = Step.parse({"op": "press", "button": "right", "duration_ms": 10})
    await engine.run([press])
    fake.fail_next = RuntimeError("stream closed")
    with pytest.raises(InputAbortedError, match=r"after 0 of 3 steps \(stream closed\)") as info:
        await engine.run([press] * 3)
    assert info.value.done == 0 and info.value.total == 3
    assert fake.index == 1  # nothing after the failure was sent
    assert fake.idle


def test_menu_buttons_get_a_longer_default_press():
    assert Step.parse({"op": "press", "button": "options"}).duration_ms == 250
    assert Step.parse({"op": "press", "button": "cross"}).duration_ms == 80
    assert Step.parse({"op": "press", "button": "options", "duration_ms": 40}).duration_ms == 40


# --- daemon --------------------------------------------------------------
async def _until(predicate, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.02)


async def test_stalled_stream_is_reported_and_input_refused(daemon, client, fake):
    daemon.ready_timeout_s = 0.3
    fake.stalled = True
    await _until(lambda: daemon.state == "stalled")
    assert (await client.status())["daemon"] == "stalled"
    sent = len(fake.log)
    with pytest.raises(DaemonError, match="not live"):
        await client.input("press", button="cross")
    assert len(fake.log) == sent  # refused, not queued for later
    fake.stalled = False
    assert (await client.wait_ready(5))["ready"] is True
    assert (await client.status())["daemon"] == "up"


async def test_unexpected_daemon_error_is_readable(daemon, client, monkeypatch):
    async def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(daemon, "status_dict", broken)
    with pytest.raises(DaemonError, match="RuntimeError: boom"):
        await client.status()


async def test_until_change_returns_once_the_screen_changes(client, fake):
    t0 = time.monotonic()
    snap = await client.input("press", button="right", snapshot_after_ms=5_000, until_change=True)
    assert time.monotonic() - t0 < 2.0
    assert snap.screen_changed is True and "screen_changed=true" in snap.describe()
    snap = await client.input("press", button="cross", snapshot_after_ms=50)
    assert snap.screen_changed is False


async def test_idle_daemon_exits(cfg, fake):
    from dataclasses import replace

    d = Daemon(replace(cfg, idle_exit_s=1), fake)
    stop = asyncio.Event()
    await asyncio.wait_for(exit_when_idle(d, stop), timeout=3)
    assert stop.is_set()


# --- MCP: wait_ready in-process, disconnect against a real daemon process ---
async def test_mcp_wait_ready(cfg, daemon):
    dc = DaemonClient(cfg, autostart=False)
    try:
        async with Client(build_server(cfg, dc)) as mcp_client:
            assert "stream is live" in _text(await mcp_client.call_tool("wait_ready", {"timeout_s": 2}))
    finally:
        await dc.aclose()


async def test_mcp_disconnect_stops_daemon_and_next_call_restarts_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PS5_BACKEND", "fake")
    monkeypatch.setenv("PS5_DAEMON_ADDR", f"127.0.0.1:{free_port()}")
    monkeypatch.setenv("PS5_CONFIG_DIR", str(tmp_path))
    cfg = from_env()
    dc = DaemonClient(cfg)
    try:
        async with Client(build_server(cfg, dc)) as mcp_client:
            assert "session_up" in _text(await mcp_client.call_tool("status", {}))
            assert "disconnected" in _text(await mcp_client.call_tool("disconnect", {}))
            assert "not connected" in _text(await mcp_client.call_tool("disconnect", {}))
            assert "session_up" in _text(await mcp_client.call_tool("status", {}))  # autostarted again
            assert "disconnected" in _text(await mcp_client.call_tool("disconnect", {}))
    finally:
        await dc.aclose()


# --- vendored pyremoteplay ----------------------------------------------
async def test_stream_sends_from_other_threads_run_on_the_loop():
    class Transport:
        def __init__(self) -> None:
            self.threads: list[int] = []

        def is_closing(self) -> bool:
            return False

        def sendto(self, data, addr) -> None:
            self.threads.append(threading.get_ident())

    protocol = RPStream.Protocol(stream=None)
    transport = Transport()
    protocol.connection_made(transport)
    worker = threading.Thread(target=protocol.sendto, args=(b"x", ("127.0.0.1", 9)))
    worker.start()
    worker.join()
    protocol.sendto(b"y", ("127.0.0.1", 9))
    await _until(lambda: len(transport.threads) == 2)
    assert set(transport.threads) == {threading.get_ident()}
