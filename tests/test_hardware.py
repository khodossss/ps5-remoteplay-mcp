"""Round trip against a real console through a real daemon process. Runs only with PS5_HOST set."""

import asyncio
import io
import os
import subprocess
import sys
import time

import pytest
from PIL import Image

from ps5agent.client import DaemonClient
from ps5agent.config import from_env

from .conftest import free_port

pytestmark = [
    pytest.mark.hardware,
    pytest.mark.skipif(not os.environ.get("PS5_HOST"), reason="needs a real PS5: set PS5_HOST"),
]


@pytest.fixture
def hw_daemon():
    port = free_port()
    env = {**os.environ, "PS5_DAEMON_ADDR": f"127.0.0.1:{port}"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ps5agent", "daemon"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    yield from_env(env)
    try:
        proc.wait(timeout=15)  # the test asks the daemon to shut down
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
async def hw_client(hw_daemon):
    client = DaemonClient(hw_daemon, autostart=False)
    deadline = time.monotonic() + 40
    while True:
        try:
            if (await client.status())["session_up"]:
                break
        except Exception:
            pass
        if time.monotonic() > deadline:
            pytest.fail("session did not come up in 40 s (another session already running?)")
        await asyncio.sleep(1)
    yield client
    await client.shutdown()
    await client.aclose()


async def test_snapshot_press_release(hw_client):
    snap = await hw_client.snapshot()
    assert Image.open(io.BytesIO(snap.jpeg)).format == "JPEG"
    assert snap.age_ms < 2000
    after = await hw_client.input("press", button="right", snapshot_after_ms=600)
    assert after.frame_id > snap.frame_id
    await hw_client.input("press", button="left")
    await hw_client.input("release_all")
