from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from aiohttp import web

from ps5_remoteplay_mcp import config
from ps5_remoteplay_mcp.backend.fake import FakeBackend
from ps5_remoteplay_mcp.config import Config
from ps5_remoteplay_mcp.daemon import Daemon, build_app, make_runner


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_until(predicate: Callable[[], bool], timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.005)


@pytest.fixture(autouse=True)
def _home_config(request, tmp_path_factory, monkeypatch):
    """Keep the default config folder out of the real home; hardware tests need the real credentials."""
    if request.node.get_closest_marker("hardware") is None:
        monkeypatch.setattr(config, "CONFIG_BASE", tmp_path_factory.mktemp("home"))


@pytest.fixture
def fake() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(backend="fake", daemon_port=free_port(), max_hold_ms=2_000, config_dir=tmp_path)


@pytest.fixture
async def daemon(cfg: Config, fake: FakeBackend):
    """A running daemon on a real loopback port, backed by FakeBackend."""
    d = Daemon(cfg, fake)
    app = build_app(d)
    runner = make_runner(app)
    await runner.setup()
    await web.TCPSite(runner, cfg.daemon_host, cfg.daemon_port).start()
    await d.start()
    for _ in range(100):  # supervisor connects the fake session
        if fake.connected:
            break
        await asyncio.sleep(0.01)
    yield d
    await d.stop()
    await runner.cleanup()
