"""`ps5agent` CLI: setup | daemon | status | snapshot | press | latency | stop."""

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


GUI_UNAVAILABLE = 3


def cmd_setup(cfg: Config, args: argparse.Namespace) -> None:
    """Find the console and link this computer to it: PSN sign-in, then the PIN from the TV."""
    import webbrowser

    from . import console
    from .backend.base import ConsoleError

    console.prepare(cfg)
    try:
        host = asyncio.run(console.resolve_host(cfg, rediscover=not cfg.host))
    except ConsoleError as err:
        sys.exit(f"error: {err}")
    print(f"PS5 found at {host}")
    if console.is_set_up(cfg) and not args.force:
        print("this computer is already linked; pass --force to link another PSN user")
        return
    user = console.pending_user(cfg)
    if not args.no_gui:
        try:
            from . import login_window

            linked = login_window.run(cfg, host, user)
        except Exception as err:  # no webview backend (headless Linux, missing WebView2, ...)
            print(f"sign-in window unavailable ({err}); falling back to the browser")
        else:
            if not linked:
                sys.exit("setup was not completed")
            print(f"linked; credentials saved in {cfg.profile_path}")
            return
    if args.gui_only or not sys.stdin.isatty():
        sys.exit(GUI_UNAVAILABLE)
    try:
        if user is None:
            url = console.login_url()
            print(f"1. Sign in to PSN in your browser (opening it now):\n\n{url}\n")
            webbrowser.open(url)
            redirect = input(
                "2. Paste the full address of the page you land on (may look like an error):\n> "
            )
            user = console.add_user_from_redirect(cfg, redirect)
            print(f"signed in as {user}")
        pin = input("3. On the PS5: Settings > System > Remote Play > Link Device. 8-digit PIN:\n> ")
        console.link(cfg, host, user, pin)
    except ConsoleError as err:
        sys.exit(f"error: {err}")
    print(f"linked; credentials saved in {cfg.profile_path}")


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
    p = sub.add_parser("setup", aliases=["register"], help="find the console and link this computer to it")
    p.add_argument("--force", action="store_true", help="link even if already linked")
    p.add_argument("--no-gui", action="store_true", help="use the browser + terminal instead of a window")
    p.add_argument("--gui-only", action="store_true", help=argparse.SUPPRESS)  # used by the MCP setup tool
    p.set_defaults(fn=cmd_setup)
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


def entry() -> None:
    """`ps5-remoteplay-mcp`: no arguments runs the MCP server (what agents launch); otherwise the CLI."""
    if len(sys.argv) > 1:
        main()
    else:
        from .mcp_server import main as mcp_main

        mcp_main()
