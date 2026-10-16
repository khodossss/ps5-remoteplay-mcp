"""Daemon HTTP API and MCP contract tests against FakeBackend."""

import asyncio
import base64
import contextlib
import io

import pytest
from mcp import Client
from mcp.types import ImageContent, TextContent
from PIL import Image

from ps5agent.backend import ConsoleState
from ps5agent.client import DaemonClient, DaemonError
from ps5agent.mcp_server import build_server


@pytest.fixture
async def client(cfg, daemon):
    c = DaemonClient(cfg, autostart=False)
    yield c
    await c.aclose()


async def test_status(client):
    status = await client.status()
    assert status["daemon"] == "up" and status["session_up"] is True and status["console"] == "awake"


async def test_press_with_snapshot_after(client, fake):
    snap = await client.input("press", button="right", snapshot_after_ms=10)
    assert fake.index == 1 and fake.idle
    assert Image.open(io.BytesIO(snap.jpeg)).format == "JPEG"
    assert snap.frame_id == fake.frame_id and snap.warnings == []


async def test_bad_request_is_reported(client):
    with pytest.raises(DaemonError, match="bad request"):
        await client.input("press", button="start")
    with pytest.raises(DaemonError, match="duration_ms"):
        await client.input("hold", button="r2", duration_ms=60_000)


async def test_client_disconnect_mid_hold_releases(cfg, client, fake):
    task = asyncio.create_task(client.input("hold", button="l1", duration_ms=1_500))
    await asyncio.sleep(0.2)
    assert fake.pressed
    task.cancel()  # client goes away; aiohttp cancels the handler
    await asyncio.sleep(0.3)
    assert fake.idle


async def test_daemon_stop_mid_hold_releases(daemon, client, fake):
    task = asyncio.create_task(client.input("hold", button="r1", duration_ms=1_500))
    await asyncio.sleep(0.2)
    await daemon.stop()
    assert fake.idle
    task.cancel()


async def test_standby_then_wake(client, daemon, fake):
    await client.standby()
    assert fake.awake is False
    await asyncio.sleep(0.05)
    assert (await fake.status()).console is ConsoleState.STANDBY
    with pytest.raises(DaemonError, match="no frame"):
        await client.snapshot()
    await client.wake()
    for _ in range(100):
        if fake.connected:
            break
        await asyncio.sleep(0.02)
    assert (await client.snapshot()).jpeg


async def test_black_frame_warning(client, fake):
    import numpy as np

    fake._frames[fake.index] = np.zeros_like(fake._frames[fake.index])
    snap = await client.snapshot()
    assert any("black" in w for w in snap.warnings)


# --- MCP contract --------------------------------------------------------
TOOLS = {
    "status",
    "snapshot",
    "press",
    "hold",
    "stick",
    "trigger",
    "sequence",
    "release_all",
    "wait_ready",
    "disconnect",
    "wake",
    "standby",
    "setup",
    "viewer",
}


def _image(result) -> bytes:
    images = [c for c in result.content if isinstance(c, ImageContent)]
    assert len(images) == 1 and images[0].mime_type == "image/jpeg"
    data = base64.b64decode(images[0].data)
    assert Image.open(io.BytesIO(data)).format == "JPEG"
    return data


def _text(result) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


@contextlib.asynccontextmanager
async def mcp_session(cfg):
    """MCP client in-process; must be entered and exited in the test's own task (anyio)."""
    dc = DaemonClient(cfg, autostart=False)
    try:
        async with Client(build_server(cfg, dc)) as c:
            yield c
    finally:
        await dc.aclose()


async def test_mcp_lists_all_tools(cfg, daemon):
    async with mcp_session(cfg) as mcp_client:
        await _test_mcp_lists_all_tools(mcp_client)


async def _test_mcp_lists_all_tools(mcp_client):
    tools = {t.name: t.annotations for t in (await mcp_client.list_tools()).tools}
    assert set(tools) == TOOLS
    read = (True, None, None, False)
    hints = {
        n: (a.read_only_hint, a.destructive_hint, a.idempotent_hint, a.open_world_hint)
        for n, a in tools.items()
    }
    # (read_only, destructive, idempotent, open_world); None is the spec default, so it is spelled out.
    assert hints == {
        "status": read,
        "wait_ready": read,
        "snapshot": read,
        **dict.fromkeys(["press", "hold", "stick", "trigger", "sequence"], (None, True, False, True)),
        **dict.fromkeys(["release_all", "viewer", "wake"], (None, False, True, False)),
        **dict.fromkeys(["disconnect", "standby"], (None, True, True, False)),
        "setup": (None, False, False, True),
    }


async def test_mcp_every_tool(cfg, daemon, fake):
    async with mcp_session(cfg) as mcp_client:
        await _test_mcp_every_tool(mcp_client, fake)


async def _test_mcp_every_tool(mcp_client, fake):
    call = mcp_client.call_tool
    assert "session_up: True" in _text(await call("status", {}))
    snap = await call("snapshot", {"max_width": 640})
    _image(snap)
    assert "frame_id=" in _text(snap)
    r = await call("press", {"button": "right", "snapshot_after_ms": 20})
    _image(r)
    assert fake.index == 1
    assert "held" in _text(await call("hold", {"button": "square", "duration_ms": 30}))
    _image(
        await call("stick", {"side": "left", "x": 0.5, "y": -0.5, "duration_ms": 20, "snapshot_after_ms": 0})
    )
    assert "trigger" in _text(await call("trigger", {"side": "right", "value": 1.0, "duration_ms": 20}))
    seq = await call(
        "sequence",
        {
            "steps": [
                {"op": "press", "button": "right"},
                {"op": "wait", "duration_ms": 10},
                {"op": "press", "button": "left"},
            ],
            "snapshot_after_ms": 10,
        },
    )
    _image(seq)
    assert fake.index == 1
    assert "released" in _text(await call("release_all", {}))
    assert "awake" in _text(await call("wake", {}))
    assert fake.idle


async def test_mcp_tool_error_is_reported(cfg, daemon):
    async with mcp_session(cfg) as mcp_client:
        await _test_mcp_tool_error_is_reported(mcp_client)


async def _test_mcp_tool_error_is_reported(mcp_client):
    result = await mcp_client.call_tool("hold", {"button": "cross", "duration_ms": 9_000})
    assert result.is_error and "duration_ms" in _text(result)


async def test_mcp_standby_requires_confirm(cfg, daemon, fake):
    async with mcp_session(cfg) as mcp_client:
        await _test_mcp_standby_requires_confirm(mcp_client, fake)


async def _test_mcp_standby_requires_confirm(mcp_client, fake):
    result = await mcp_client.call_tool("standby", {})
    assert result.is_error and fake.awake


async def test_input_waits_for_session_while_connecting(cfg, fake):
    """First call right after autostart must wait for the session instead of failing."""
    from ps5agent.daemon import Daemon

    d = Daemon(cfg, fake)
    d.state = "connecting"

    async def connect_later():
        await asyncio.sleep(0.3)
        await fake.connect()
        d.state = "up"

    task = asyncio.create_task(connect_later())
    snap = await d.snapshot()
    await task
    assert snap["frame_id"] == fake.frame_id
