"""Finding the console and linking this host to it (PSN sign-in + PIN)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from .backend.base import ConsoleError
from .config import Config

REDIRECT_PREFIX = "https://remoteplay.dl.playstation.net/remoteplay/redirect"
_VENDOR_LOGGER = "ps5agent.vendor.pyremoteplay"


def prepare(cfg: Config) -> None:
    """Point the protocol library at our credentials file and keep its secrets out of logs."""
    from .vendor.pyremoteplay.profile import Profiles

    # DEBUG logs of the protocol library print the handshake key and ECDH secret.
    vendor = logging.getLogger(_VENDOR_LOGGER)
    vendor.setLevel(max(logging.INFO, vendor.level))
    cfg.config_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(cfg.config_dir, 0o700)
        if cfg.profile_path.exists():
            os.chmod(cfg.profile_path, 0o600)
    Profiles.set_default_path(str(cfg.profile_path))


def _profiles():
    from .vendor.pyremoteplay.profile import Profiles

    return Profiles.load()


def registered_macs() -> set[str]:
    return {host.name for user in _profiles().users for host in user.hosts}


def is_set_up(cfg: Config) -> bool:
    prepare(cfg)
    return bool(registered_macs())


@dataclass(frozen=True)
class Found:
    host: str
    name: str
    host_type: str
    status: str
    mac: str


async def discover() -> list[Found]:
    from .vendor.pyremoteplay import RPDevice

    devices = await RPDevice.async_search()
    return [
        Found(d.host, d.host_name or "", d.host_type or "", d.status_name or "", d.mac_address or "")
        for d in devices
    ]


def _saved_path(cfg: Config):
    return cfg.config_dir / "console.json"


def save_host(cfg: Config, found: Found) -> None:
    cfg.config_dir.mkdir(parents=True, exist_ok=True)
    _saved_path(cfg).write_text(json.dumps({"host": found.host, "name": found.name, "mac": found.mac}))


def saved_host(cfg: Config) -> str | None:
    try:
        return json.loads(_saved_path(cfg).read_text())["host"]
    except (OSError, ValueError, KeyError):
        return None


async def resolve_host(cfg: Config, rediscover: bool = False) -> str:
    """PS5_HOST if set; else the saved console; else discover — preferring a registered console."""
    if cfg.host:
        return cfg.host
    saved = None if rediscover else saved_host(cfg)
    if saved:
        return saved
    found = [f for f in await discover() if f.host_type == "PS5"]
    if not found:
        raise ConsoleError(
            "no PS5 found on the local network; is it on and on the same subnet? (or set PS5_HOST)"
        )
    macs = registered_macs()
    known = [f for f in found if f.mac in macs]
    pick = (known or found)[0]
    save_host(cfg, pick)
    return pick.host


# --- linking: PSN sign-in, then PIN ------------------------------------------
def login_url() -> str:
    from .vendor.pyremoteplay.oauth import get_login_url

    return get_login_url()


def add_user_from_redirect(cfg: Config, redirect_url: str) -> str:
    """Exchange the URL Sony redirected to after sign-in for a stored PSN user; returns its name."""
    prepare(cfg)
    if not redirect_url.startswith(REDIRECT_PREFIX) or "code=" not in redirect_url:
        raise ConsoleError("that is not the page Sony redirects to after sign-in (it must contain code=)")
    profile = _profiles().new_user(redirect_url.strip())
    if profile is None:
        raise ConsoleError("Sony rejected the sign-in code; sign in again (codes expire within minutes)")
    return profile.name


def pending_user(cfg: Config) -> str | None:
    """A signed-in PSN user that is not yet linked to any console."""
    prepare(cfg)
    for user in _profiles().users:
        if not user.hosts:
            return user.name
    return None


def link(cfg: Config, host: str, user: str, pin: str) -> None:
    """Register this host with the console using the PIN from Settings > System > Remote Play."""
    from .vendor.pyremoteplay import RPDevice

    prepare(cfg)
    pin = pin.strip().replace(" ", "")
    if not (pin.isdigit() and len(pin) == 8):
        raise ConsoleError("the PIN must be 8 digits")
    device = RPDevice(host)
    if not device.get_status():
        raise ConsoleError(f"console at {host} is not reachable")
    profiles = _profiles()
    if not device.register(user, pin, profiles=profiles):
        raise ConsoleError("the console rejected the PIN (it changes every few minutes; get a new one)")
    prepare(cfg)  # re-apply file permissions after save
