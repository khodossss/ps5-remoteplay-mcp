"""Viewer window feed: /frame serves the newest frame to the human's window without keeping the
daemon alive."""

import io

import httpx
from PIL import Image

from ps5agent.config import from_env


async def test_frame_serves_new_frames_only(cfg, daemon, fake):
    async with httpx.AsyncClient(base_url=cfg.daemon_url) as http:
        response = await http.get("/frame")
        assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
        frame_id = int(response.headers["x-frame-id"])
        assert frame_id == fake.frame_id
        assert Image.open(io.BytesIO(response.content)).format == "JPEG"
        assert (await http.get("/frame", params={"after": frame_id})).status_code == 204
        # a reconnect restarts frame ids: a lower id is a new frame too
        assert (await http.get("/frame", params={"after": frame_id + 100})).status_code == 200


async def test_frame_does_not_keep_daemon_alive(cfg, daemon):
    daemon.last_call = 0.0
    async with httpx.AsyncClient(base_url=cfg.daemon_url) as http:
        assert (await http.get("/frame")).status_code == 200
    assert daemon.last_call == 0.0


async def test_frame_without_video(cfg, daemon, fake):
    fake.awake = False
    await fake.disconnect()
    async with httpx.AsyncClient(base_url=cfg.daemon_url) as http:
        assert (await http.get("/frame")).status_code == 204


def test_viewer_option_from_env():
    assert from_env({}).viewer is False
    assert from_env({"PS5_VIEWER": "1"}).viewer is True
    assert from_env({"PS5_VIEWER": "0"}).viewer is False


class FakeWindow:
    """Stands in for the viewer process: no real window in tests."""

    def __init__(self) -> None:
        self.closed = False

    def poll(self):
        return 0 if self.closed else None

    def terminate(self) -> None:
        self.closed = True


async def test_viewer_tool_opens_and_closes_the_window(cfg, daemon):
    from mcp import Client

    from ps5agent.client import DaemonClient
    from ps5agent.mcp_server import build_server

    windows: list[FakeWindow] = []
    daemon.spawn_viewer = lambda: windows.append(FakeWindow()) or windows[-1]
    dc = DaemonClient(cfg, autostart=False)
    try:
        assert (await dc.status())["viewer"] is False  # closed unless asked for
        async with Client(build_server(cfg, dc)) as mcp_client:
            result = await mcp_client.call_tool("viewer", {"show": True})
            assert "open" in result.content[0].text and (await dc.status())["viewer"] is True
            await mcp_client.call_tool("viewer", {"show": True})
            assert len(windows) == 1  # already open: no second window
            windows[0].closed = True  # the user closed it; asking again reopens
            await mcp_client.call_tool("viewer", {"show": True})
            assert len(windows) == 2
            result = await mcp_client.call_tool("viewer", {"show": False})
            assert "closed" in result.content[0].text and windows[1].closed
    finally:
        await dc.aclose()
