"""Thin stdio MCP server: every tool is one call to the daemon."""

from __future__ import annotations

import functools
import logging
from typing import Annotated, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .backend import Button, Side
from .client import DaemonClient, DaemonError, Snapshot
from .config import Config, from_env

INSTRUCTIONS = """\
You control a real PlayStation 5 through Remote Play: you see its screen and press its gamepad.
- Take a `snapshot` before acting, and pass `snapshot_after_ms` (300-800) to input tools to see the result
  in the same call. Each round trip takes seconds: this is for menus and turn-based play, not reflexes.
- To leave a system screen or go back, press `circle`. `ps` opens the control center; `options` opens
  context menus. Confirm is `cross`.
- Never confirm a purchase in PlayStation Store, and never delete games, saves or users, unless the user
  explicitly asked for exactly that.
- If a frame is reported black, the content is likely blocked from Remote Play; say so instead of guessing.
"""

Ms = Annotated[int, Field(ge=0, le=10_000)]
SnapshotAfter = Annotated[
    int | None,
    Field(ge=0, le=10_000, description="If set, wait this long after the input and return the screen."),
]


def build_server(cfg: Config, client: DaemonClient | None = None) -> MCPServer:
    daemon = client or DaemonClient(cfg)
    mcp = MCPServer("ps5", instructions=INSTRUCTIONS, log_level="WARNING")

    def tool(fn):
        """Register a tool; daemon refusals reach the model as readable errors, not masked ones."""

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except DaemonError as err:
                raise ToolError(str(err)) from err

        return mcp.tool(structured_output=False)(wrapper)

    def show(snap: Snapshot, note: str = "") -> list:
        return [Image(data=snap.jpeg, format="jpeg"), (note + "\n" if note else "") + snap.describe()]

    async def send(op: str, snapshot_after_ms: int | None, note: str, **body) -> list | str:
        snap = await daemon.input(op, snapshot_after_ms=snapshot_after_ms, **body)
        return show(snap, note) if snap else note

    @tool
    async def status() -> str:
        """Console and session state: awake/standby, session up, fps, age of the latest frame, firmware."""
        s = await daemon.status()
        return "\n".join(f"{k}: {v}" for k, v in s.items())

    @tool
    async def snapshot(
        max_width: Annotated[int, Field(ge=160, le=1920)] = 1024,
        quality: Annotated[int, Field(ge=10, le=95)] = 80,
    ) -> list:
        """Current PS5 screen as a JPEG. Do this before acting."""
        return show(await daemon.snapshot(max_width, quality))

    @tool
    async def press(
        button: Button, duration_ms: Ms = 80, snapshot_after_ms: SnapshotAfter = None
    ) -> list | str:
        """Tap a gamepad button. cross=confirm, circle=back, d-pad up/down/left/right moves focus."""
        return await send(
            "press",
            snapshot_after_ms,
            f"pressed {button.value}",
            button=button.value,
            duration_ms=duration_ms,
        )

    @tool
    async def hold(button: Button, duration_ms: Ms, snapshot_after_ms: SnapshotAfter = None) -> list | str:
        """Hold a button for duration_ms (max 10 s), then release it."""
        return await send(
            "hold",
            snapshot_after_ms,
            f"held {button.value} {duration_ms} ms",
            button=button.value,
            duration_ms=duration_ms,
        )

    @tool
    async def stick(
        side: Side,
        x: Annotated[float, Field(ge=-1, le=1, description="-1 left .. 1 right")],
        y: Annotated[float, Field(ge=-1, le=1, description="-1 up .. 1 down")],
        duration_ms: Ms = 300,
        snapshot_after_ms: SnapshotAfter = None,
    ) -> list | str:
        """Deflect an analog stick for duration_ms, then return it to center."""
        return await send(
            "stick",
            snapshot_after_ms,
            f"{side.value} stick ({x}, {y}) {duration_ms} ms",
            side=side.value,
            x=x,
            y=y,
            duration_ms=duration_ms,
        )

    @tool
    async def trigger(
        side: Side,
        value: Annotated[float, Field(ge=0, le=1, description="0 released .. 1 fully pressed")],
        duration_ms: Ms = 300,
        snapshot_after_ms: SnapshotAfter = None,
    ) -> list | str:
        """Press analog L2 (left) or R2 (right) to `value` for duration_ms, then release."""
        return await send(
            "trigger",
            snapshot_after_ms,
            f"{side.value} trigger {value} {duration_ms} ms",
            side=side.value,
            value=value,
            duration_ms=duration_ms,
        )

    @tool
    async def sequence(
        steps: Annotated[
            list[dict],
            Field(
                description=(
                    'Steps run in order, e.g. [{"op":"press","button":"down"},'
                    '{"op":"wait","duration_ms":400},{"op":"press","button":"cross"}]. '
                    "ops: press/hold {button,duration_ms}, "
                    "stick {side,x,y,duration_ms}, trigger {side,value,duration_ms}, wait {duration_ms}. "
                    "Max 100 steps, 60 s total."
                )
            ),
        ],
        snapshot_after_ms: SnapshotAfter = None,
    ) -> list | str:
        """Run several inputs in one call (e.g. navigate a menu), optionally returning the screen after."""
        return await send("sequence", snapshot_after_ms, f"ran {len(steps)} steps", steps=steps)

    @tool
    async def release_all() -> str:
        """Release every button, center both sticks, release triggers."""
        await daemon.input("release_all")
        return "all input released"

    @tool
    async def wake() -> str:
        """Wake the console from rest mode (takes up to a minute), then the session reconnects."""
        await daemon.wake()
        return "console is awake; the session reconnects within a few seconds - check `status`"

    @tool
    async def standby(confirm: Literal["yes"]) -> str:
        """Put the console into rest mode. Only when the user asked for it; pass confirm="yes"."""
        await daemon.standby()
        return "console is going to rest mode"

    return mcp


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_server(from_env()).run("stdio")


if __name__ == "__main__":
    main()
