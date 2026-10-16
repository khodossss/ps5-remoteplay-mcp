"""Thin stdio MCP server: every tool is one call to the daemon."""

from __future__ import annotations

import asyncio
import functools
import logging
import subprocess
import sys
from typing import Annotated, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import console
from .backend import Button, ConsoleError, Side
from .client import DaemonClient, DaemonError, Snapshot
from .config import Config, from_env

INSTRUCTIONS = """\
You control a real PlayStation 5 through Remote Play: you see its screen and press its gamepad.
- Take a `snapshot` before acting, and pass `snapshot_after_ms` (300-800) to input tools to see the result
  in the same call. Each round trip takes seconds: this is for menus and turn-based play, not reflexes.
- After input the snapshot says `screen_changed`. For slow screens (searches, loading) pass
  `until_change=true` with a long `snapshot_after_ms` (up to 10000): it returns as soon as the screen
  changes. If the screen did not change, take another snapshot before pressing again - a repeated press
  may trigger the next action.
- If `status` says `stalled` or `reconnecting`, call `wait_ready` instead of sleeping. A failed input
  reports how many steps ran; the rest was dropped, never sent later - check the screen, then continue.
- A gamepad connected to the computer takes priority: human_input in status means agent input is
  refused. Wait for the user to finish; interrupted steps are dropped and must not be retried blindly.
- To hand the console back to a controller attached directly to the PS5, call `disconnect`.
  If the user plays with a computer gamepad, leave the daemon running: disconnect would stop their
  input too. It disconnects by itself after 10 minutes without calls or computer-gamepad activity.
- To leave a system screen or go back, press `circle`. `ps` opens the control center; `options` opens
  context menus. Confirm is `cross`.
- Never confirm a purchase in PlayStation Store, and never delete games, saves or users, unless the user
  explicitly asked for exactly that.
- If a frame is reported black, the content is likely blocked from Remote Play; say so instead of guessing.
- The user watches the TV by default. Open the `viewer` window on their computer only when they ask to
  see the screen ("turn it on with the screen", "show me"); close it when they ask.
- First run: if `status` says not_set_up, call `setup` and tell the user a PSN sign-in window opened on
  their screen; they sign in there and type the PIN shown on the PS5.
"""

Ms = Annotated[int, Field(ge=0, le=10_000)]
SnapshotAfter = Annotated[
    int | None,
    Field(ge=0, le=10_000, description="If set, wait this long after the input and return the screen."),
]
UntilChange = Annotated[
    bool,
    Field(description="With snapshot_after_ms: return as soon as the screen changes (that is the max wait)."),
]

# Hints for the client on when to ask the user (graph `ps5-agent-bridge`, node #32). The console is a
# closed world on the LAN; only setup reaches PSN. Any input may confirm a dialog, so it is destructive.
READS = ToolAnnotations(read_only_hint=True, open_world_hint=False)
INPUT = ToolAnnotations(destructive_hint=True, idempotent_hint=False, open_world_hint=False)
SAFE_CHANGE = ToolAnnotations(destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def build_server(cfg: Config, client: DaemonClient | None = None) -> MCPServer:
    daemon = client or DaemonClient(cfg)
    mcp = MCPServer("ps5", instructions=INSTRUCTIONS, log_level="WARNING")

    def tool(annotations: ToolAnnotations):
        """Register a tool; daemon refusals reach the model as readable errors, not masked ones."""

        def register(fn):
            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except (DaemonError, ConsoleError) as err:
                    raise ToolError(str(err)) from err

            return mcp.tool(structured_output=False, annotations=annotations)(wrapper)

        return register

    def show(snap: Snapshot, note: str = "") -> list:
        return [Image(data=snap.jpeg, format="jpeg"), (note + "\n" if note else "") + snap.describe()]

    async def send(
        op: str, snapshot_after_ms: int | None, until_change: bool, note: str, **body
    ) -> list | str:
        snap = await daemon.input(
            op, snapshot_after_ms=snapshot_after_ms, until_change=until_change or None, **body
        )
        return show(snap, note) if snap else note

    @tool(READS)
    async def status() -> str:
        """Console and session state. daemon: up | stalled (session open, no new frames) | reconnecting |
        standby | unreachable | not_set_up; plus fps, age of the latest frame, firmware."""
        s = await daemon.status()
        return "\n".join(f"{k}: {v}" for k, v in s.items())

    @tool(READS)
    async def wait_ready(timeout_s: Annotated[int, Field(ge=1, le=60)] = 30) -> str:
        """Wait until the video stream is live (after a reconnect or a stall), up to timeout_s."""
        s = await daemon.wait_ready(timeout_s)
        head = "stream is live" if s.pop("ready") else f"stream still not live after {timeout_s} s"
        return head + "\n" + "\n".join(f"{k}: {v}" for k, v in s.items())

    @tool(READS)
    async def snapshot(
        max_width: Annotated[int, Field(ge=160, le=1920)] = 1024,
        quality: Annotated[int, Field(ge=10, le=95)] = 80,
    ) -> list:
        """Current PS5 screen as a JPEG. Do this before acting."""
        return show(await daemon.snapshot(max_width, quality))

    @tool(INPUT)
    async def press(
        button: Button,
        duration_ms: Annotated[
            int | None,
            Field(ge=0, le=10_000, description="Default 80 ms; 250 ms for options/create/touchpad/ps."),
        ] = None,
        snapshot_after_ms: SnapshotAfter = None,
        until_change: UntilChange = False,
    ) -> list | str:
        """Tap a gamepad button. cross=confirm, circle=back, d-pad up/down/left/right moves focus."""
        return await send(
            "press",
            snapshot_after_ms,
            until_change,
            f"pressed {button.value}",
            button=button.value,
            duration_ms=duration_ms,
        )

    @tool(INPUT)
    async def hold(
        button: Button,
        duration_ms: Ms,
        snapshot_after_ms: SnapshotAfter = None,
        until_change: UntilChange = False,
    ) -> list | str:
        """Hold a button for duration_ms (max 10 s), then release it."""
        return await send(
            "hold",
            snapshot_after_ms,
            until_change,
            f"held {button.value} {duration_ms} ms",
            button=button.value,
            duration_ms=duration_ms,
        )

    @tool(INPUT)
    async def stick(
        side: Side,
        x: Annotated[float, Field(ge=-1, le=1, description="-1 left .. 1 right")],
        y: Annotated[float, Field(ge=-1, le=1, description="-1 up .. 1 down")],
        duration_ms: Ms = 300,
        snapshot_after_ms: SnapshotAfter = None,
        until_change: UntilChange = False,
    ) -> list | str:
        """Deflect an analog stick for duration_ms, then return it to center."""
        return await send(
            "stick",
            snapshot_after_ms,
            until_change,
            f"{side.value} stick ({x}, {y}) {duration_ms} ms",
            side=side.value,
            x=x,
            y=y,
            duration_ms=duration_ms,
        )

    @tool(INPUT)
    async def trigger(
        side: Side,
        value: Annotated[float, Field(ge=0, le=1, description="0 released .. 1 fully pressed")],
        duration_ms: Ms = 300,
        snapshot_after_ms: SnapshotAfter = None,
        until_change: UntilChange = False,
    ) -> list | str:
        """Press analog L2 (left) or R2 (right) to `value` for duration_ms, then release."""
        return await send(
            "trigger",
            snapshot_after_ms,
            until_change,
            f"{side.value} trigger {value} {duration_ms} ms",
            side=side.value,
            value=value,
            duration_ms=duration_ms,
        )

    @tool(INPUT)
    async def sequence(
        steps: Annotated[
            list[dict],
            Field(
                description=(
                    'Steps run in order, e.g. [{"op":"press","button":"down"},'
                    '{"op":"wait","duration_ms":400},{"op":"press","button":"cross"}]. '
                    "ops: press/hold {button,duration_ms}, "
                    "stick {side,x,y,duration_ms}, trigger {side,value,duration_ms}, wait {duration_ms}. "
                    "Max 100 steps, 60 s total. If a step fails, the error says how many ran; "
                    "the rest is dropped."
                )
            ),
        ],
        snapshot_after_ms: SnapshotAfter = None,
        until_change: UntilChange = False,
    ) -> list | str:
        """Run several inputs in one call (e.g. navigate a menu), optionally returning the screen after."""
        return await send("sequence", snapshot_after_ms, until_change, f"ran {len(steps)} steps", steps=steps)

    @tool(SAFE_CHANGE)
    async def release_all() -> str:
        """Release every button, center both sticks, release triggers."""
        await daemon.input("release_all")
        return "all input released"

    @tool(SAFE_CHANGE)
    async def disconnect() -> str:
        """End the Remote Play session and stop the background daemon, handing the console back to the
        user. Any later tool call reconnects (takes a few seconds)."""
        stopped = await daemon.shutdown()
        return "disconnected from the PS5" if stopped else "was not connected"

    @tool(SAFE_CHANGE)
    async def viewer(show: bool) -> str:
        """Open (show=true) or close a window on the user's computer with the live PS5 picture, for the
        user to watch. Closed by default: open it only when the user asks to see the screen."""
        opened = await daemon.viewer(show)
        return "picture window is open on the user's computer" if opened else "picture window is closed"

    @tool(SAFE_CHANGE)
    async def wake() -> str:
        """Wake the console from rest mode (takes up to a minute), then the session reconnects."""
        await daemon.wake()
        return "console is awake; the session reconnects within a few seconds - check `status`"

    @tool(ToolAnnotations(destructive_hint=True, idempotent_hint=True, open_world_hint=False))
    async def standby(confirm: Literal["yes"]) -> str:
        """Put the console into rest mode. Only when the user asked for it; pass confirm="yes"."""
        await daemon.standby()
        return "console is going to rest mode"

    @tool(ToolAnnotations(destructive_hint=False, idempotent_hint=False, open_world_hint=True))
    async def setup(
        redirect_url: Annotated[
            str | None,
            Field(
                description="Fallback flow only: the full address the browser landed on after PSN sign-in."
            ),
        ] = None,
        pin: Annotated[
            str | None, Field(description="Fallback flow only: the 8-digit PIN from the PS5.")
        ] = None,
    ) -> str:
        """First-time setup: find the PS5 on the network and link this computer to it. Opens a window on
        the user's screen for PSN sign-in and the PIN shown on the TV. Call when status says not_set_up."""
        return await run_setup(cfg, redirect_url, pin)

    return mcp


async def run_setup(cfg: Config, redirect_url: str | None = None, pin: str | None = None) -> str:
    from .cli import GUI_UNAVAILABLE

    console.prepare(cfg)
    host = await console.resolve_host(cfg, rediscover=not cfg.host)
    if pin:
        user = console.pending_user(cfg)
        if user is None:
            raise ConsoleError(
                "sign in first: call setup(redirect_url=...) with the address after PSN sign-in"
            )
        await asyncio.to_thread(console.link, cfg, host, user, pin)
        return f"Linked to the PS5 at {host}. The session connects in a few seconds; take a snapshot."
    if redirect_url:
        user = await asyncio.to_thread(console.add_user_from_redirect, cfg, redirect_url)
        return (
            f"Signed in as {user}. Ask the user to open Settings > System > Remote Play > Link Device on "
            "the PS5 and tell you the 8-digit PIN, then call setup(pin=...)."
        )
    if console.is_set_up(cfg):
        return f"Already linked to the PS5 at {host}."
    # The window needs the main thread of its own process; stdin must not be the MCP stdio pipe.
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "ps5agent", "setup", "--gui-only",
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )  # fmt: skip
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=900)
    except TimeoutError:
        proc.kill()
        raise ConsoleError("the sign-in window was open for 15 minutes without finishing") from None
    if proc.returncode == 0:
        return f"Linked to the PS5 at {host}. The session connects in a few seconds; take a snapshot."
    if proc.returncode == GUI_UNAVAILABLE:
        return (
            "No sign-in window can be opened here, so do it in chat. 1) Give the user this link to sign in "
            f"to PSN: {console.login_url()} 2) Ask them to paste the full address of the page they land on "
            "(it may look like an error page) and call setup(redirect_url=...). 3) Then the PIN."
        )
    tail = out.decode(errors="replace").strip().splitlines()[-1:] or ["unknown error"]
    raise ConsoleError(f"setup did not complete: {tail[0]}")


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_server(from_env()).run("stdio")


if __name__ == "__main__":
    main()
