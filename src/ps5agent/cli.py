"""`ps5agent` CLI: daemon | status | snapshot | press | latency | register | stop."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .backend import Button
from .config import Config, from_env


def _config(args: argparse.Namespace) -> Config:
    return from_env().with_overrides(
        host=args.host,
        user=args.user,
        backend=args.backend,
        resolution=args.resolution,
        fps=args.fps,
        quality=args.quality,
    )


def _run(coro_fn, cfg: Config):
    from .client import DaemonClient

    async def main():
        client = DaemonClient(cfg)
        try:
            return await coro_fn(client)
        finally:
            await client.aclose()

    return asyncio.run(main())


def cmd_daemon(cfg: Config, args: argparse.Namespace) -> None:
    from .daemon import run

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    run(cfg.validate())


def cmd_status(cfg: Config, args: argparse.Namespace) -> None:
    print(json.dumps(_run(lambda c: c.status(), cfg), indent=2, ensure_ascii=False))


def cmd_snapshot(cfg: Config, args: argparse.Namespace) -> None:
    snap = _run(lambda c: c.snapshot(args.max_width, args.jpeg_quality), cfg)
    Path(args.output).write_bytes(snap.jpeg)
    print(f"saved {args.output}: {snap.describe()}")


def cmd_press(cfg: Config, args: argparse.Namespace) -> None:
    _run(lambda c: c.input("press", button=args.button, duration_ms=args.duration_ms), cfg)
    print(f"pressed {args.button}")


def cmd_latency(cfg: Config, args: argparse.Namespace) -> None:
    print(json.dumps(_run(lambda c: c.latency(args.samples), cfg), indent=2))


def cmd_stop(cfg: Config, args: argparse.Namespace) -> None:
    from .client import DaemonClient

    async def main():
        client = DaemonClient(cfg, autostart=False)
        try:
            await client.shutdown()
        finally:
            await client.aclose()

    asyncio.run(main())
    print("daemon stopping")


def cmd_register(cfg: Config, args: argparse.Namespace) -> None:
    """Link this host to the console: PSN sign-in, then the PIN from the console screen."""
    from pyremoteplay import RPDevice
    from pyremoteplay.oauth import get_login_url
    from pyremoteplay.profile import Profiles

    from .backend.remoteplay import _patch_pyremoteplay, _use_profile_dir

    if not cfg.host:
        sys.exit("set PS5_HOST or pass --host")
    _patch_pyremoteplay()
    _use_profile_dir(cfg)
    device = RPDevice(cfg.host)
    if not device.get_status():
        sys.exit(f"console at {cfg.host} is not reachable")
    profiles = Profiles.load()
    user = cfg.user if cfg.user in profiles.usernames else None
    if user is None:
        print(f"1. Open this URL and sign in to PSN:\n\n{get_login_url()}\n")
        redirect = input(
            "2. Paste the full URL of the page you land on (it may look like an error page):\n> "
        )
        profile = profiles.new_user(redirect.strip())
        if profile is None:
            sys.exit("could not read the PSN account from that URL")
        user = profile.name
    pin = input(
        "3. On the PS5: Settings > System > Remote Play > Link Device. Enter the 8-digit PIN:\n> "
    ).strip()
    if not (pin.isdigit() and len(pin) == 8):
        sys.exit("PIN must be 8 digits")
    if not device.register(user, pin, profiles=profiles):
        sys.exit("registration failed")
    _use_profile_dir(cfg)  # re-apply file permissions after save
    print(f"registered PSN user {user}; credentials in {cfg.profile_path}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ps5agent", description="Drive a PS5 over Remote Play.")
    parser.add_argument("--host", help="console IP (PS5_HOST)")
    parser.add_argument("--user", help="registered PSN user (PS5_USER)")
    parser.add_argument("--backend", choices=["pyremoteplay", "fake"], help="PS5_BACKEND")
    parser.add_argument("--resolution", choices=["360p", "540p", "720p", "1080p"], help="PS5_RESOLUTION")
    parser.add_argument("--fps", type=int, choices=[30, 60], help="PS5_FPS")
    parser.add_argument(
        "--quality",
        choices=["default", "very_low", "low", "medium", "high", "very_high"],
        help="video bitrate (PS5_QUALITY); lower it on weak Wi-Fi",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("daemon", help="run the daemon in the foreground").set_defaults(fn=cmd_daemon)
    sub.add_parser("status", help="console and session state").set_defaults(fn=cmd_status)
    p = sub.add_parser("snapshot", help="save the current screen as JPEG")
    p.add_argument("-o", "--output", default="snapshot.jpg")
    p.add_argument("--max-width", type=int, default=1280)
    p.add_argument("--jpeg-quality", type=int, default=85)
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("press", help="tap a button")
    p.add_argument("button", choices=[b.value for b in Button])
    p.add_argument("--duration-ms", type=int, default=80)
    p.set_defaults(fn=cmd_press)
    p = sub.add_parser("latency", help="median press-to-screen latency (start on the home screen)")
    p.add_argument("-n", "--samples", type=int, default=10)
    p.set_defaults(fn=cmd_latency)
    sub.add_parser("register", help="link this host with the console (PSN sign-in + PIN)").set_defaults(
        fn=cmd_register
    )
    sub.add_parser("stop", help="stop the daemon (releases all input first)").set_defaults(fn=cmd_stop)
    args = parser.parse_args(argv)
    cfg = _config(args)
    try:
        args.fn(cfg, args)
    except Exception as err:  # surface daemon errors as one line
        if type(err).__name__ == "DaemonError":
            sys.exit(f"error: {err}")
        raise


if __name__ == "__main__":
    main()
