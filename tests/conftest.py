from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest
from aiohttp import web

from ps5agent.backend.fake import FakeBackend
from ps5agent.config import Config
from ps5agent.daemon import Daemon, build_app, make_runner


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
