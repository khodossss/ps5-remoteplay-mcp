"""Console discovery, host persistence and linking input checks (no network)."""

import asyncio

import pytest

from ps5agent import console
from ps5agent.backend import ConsoleError, ConsoleState
from ps5agent.config import Config
from ps5agent.daemon import Daemon


@pytest.fixture
def cfg(tmp_path):
    return Config(config_dir=tmp_path)


def _found(host, mac, kind="PS5"):
    return console.Found(host=host, name="PS5-1", host_type=kind, status="Ok", mac=mac)


def test_env_host_wins(tmp_path):
    cfg = Config(host="192.168.1.9", config_dir=tmp_path)
    assert asyncio.run(console.resolve_host(cfg)) == "192.168.1.9"


def test_discovery_prefers_registered_console_and_saves_it(cfg, monkeypatch):
    async def discover():
        return [_found("10.0.0.5", "AAAA"), _found("10.0.0.6", "BBBB"), _found("10.0.0.7", "CCCC", "PS4")]

    monkeypatch.setattr(console, "discover", discover)
    monkeypatch.setattr(console, "registered_macs", lambda: {"BBBB"})
    assert asyncio.run(console.resolve_host(cfg)) == "10.0.0.6"
    assert console.saved_host(cfg) == "10.0.0.6"

    async def nothing():
        raise AssertionError("must use the saved host")

    monkeypatch.setattr(console, "discover", nothing)
    assert asyncio.run(console.resolve_host(cfg)) == "10.0.0.6"


def test_no_console_found(cfg, monkeypatch):
    async def discover():
        return [_found("10.0.0.7", "CCCC", "PS4")]

    monkeypatch.setattr(console, "discover", discover)
    with pytest.raises(ConsoleError, match="no PS5 found"):
        asyncio.run(console.resolve_host(cfg))


def test_redirect_url_is_validated(cfg):
    with pytest.raises(ConsoleError, match="code="):
        console.add_user_from_redirect(cfg, "https://example.com/?code=1")
    with pytest.raises(ConsoleError, match="code="):
        console.add_user_from_redirect(cfg, console.REDIRECT_PREFIX)


@pytest.mark.parametrize("pin", ["1234", "abcdefgh", "123456789"])
def test_pin_is_validated(cfg, pin):
    with pytest.raises(ConsoleError, match="8 digits"):
        console.link(cfg, "127.0.0.1", "user", pin)


def test_fresh_install_is_not_set_up(cfg):
    assert not console.is_set_up(cfg)
    assert console.pending_user(cfg) is None


def test_interfaces_without_netifaces():
    from ps5agent.vendor.pyremoteplay import ddp

    pairs = ddp._ipv4_interfaces()
    assert pairs and all(addr and broadcast for addr, broadcast in pairs)


async def test_daemon_reports_not_set_up(cfg, fake):
    async def status():
        from ps5agent.backend.base import Status

        return Status(ConsoleState.NOT_SET_UP, False, detail="not set up yet: run the setup tool")

    fake.status = status
    d = Daemon(cfg, fake)
    await d.start()
    await asyncio.sleep(0.05)
    assert d.state == "not_set_up"
    with pytest.raises(ConsoleError, match="setup"):
        await d.snapshot()
    await d.stop()
