# PS5 Agent Bridge

An MCP server that lets an LLM agent (Claude Code, Codex, any MCP client) **see the screen of a stock PlayStation 5 and drive it like a gamepad**. It uses Sony's official Remote Play protocol over your local network: no jailbreak and no capture card.

Good for menus, settings, launching games, turn-based and slow-paced games, and "what's on screen right now?". Not for real-time action: one *frame → model → button* round trip takes seconds.

```
Claude Code ──stdio──▶ ps5agent-mcp ──HTTP (127.0.0.1)──▶ ps5agent daemon ──Remote Play (LAN)──▶ PS5
```

A long-lived **daemon** owns the single Remote Play session. The thin **MCP server** is started and restarted by your agent and talks to the daemon. Connecting to the console takes a few seconds, so the daemon survives agent restarts and keeps the session warm. It starts automatically on the first tool call.

## Requirements

- A PS5 on the same subnet as the host. Ethernet and Wi-Fi both work.
- Windows or Linux. macOS is untested.
- [uv](https://docs.astral.sh/uv/). It fetches Python 3.12 if needed.
- A PSN account signed in on the console.

## Setup

### 1. Prepare the console

- **Settings → System → Remote Play → Enable Remote Play.**
- Optional, to let the agent wake the console from rest mode: **Settings → System → Power Saving → Features Available in Rest Mode** → turn on **Stay Connected to the Internet** and **Enable Turning On PS5 from Network**.
- Recommended: **require a password for purchases** in PlayStation Store. The agent is told never to confirm purchases, but it can physically navigate there.

The console's IP address is under **Settings → System → System Software → Console Information**.

### 2. Install

```
git clone https://github.com/khodossss/ps5-agent-bridge.git
cd ps5-agent-bridge
uv sync
```

### 3. Register this computer with the console (once)

```
uv run ps5agent --host <console-ip> register
```

1. Open the printed URL and sign in to PSN.
2. You land on a page that may look like an error. Copy its **full URL** from the address bar and paste it into the terminal.
3. On the PS5, go to **Settings → System → Remote Play → Link Device** and type the 8-digit PIN.

Credentials are stored in `~/.config/ps5-agent/profiles.json`. They never appear in logs or tool responses.

### 4. Connect your agent

**Claude Code**

```
claude mcp add --env PS5_HOST=<console-ip> --transport stdio ps5 -- uv run --project /path/to/ps5-agent-bridge ps5agent-mcp
```

Add `--scope user` to make it available in every project. Check it with `claude mcp list`, or `/mcp` inside Claude Code.

**Other MCP clients** (standard `mcpServers` JSON):

```json
{
  "mcpServers": {
    "ps5": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/ps5-agent-bridge", "ps5agent-mcp"],
      "env": { "PS5_HOST": "<console-ip>" }
    }
  }
}
```

**Codex** (`~/.codex/config.toml`):

```toml
[mcp_servers.ps5]
command = "uv"
args = ["run", "--project", "/path/to/ps5-agent-bridge", "ps5agent-mcp"]
env = { PS5_HOST = "<console-ip>" }
```

## Usage

Talk to your agent in plain language:

- "What's on the PS5 screen right now?"
- "Open Settings and tell me the system software version, then go back to the home screen."
- "Launch Astro Bot and describe its main menu."
- "In the current menu, move down to *Display and Video* and open it."

The agent looks at a screenshot, presses buttons and looks again. It typically passes `snapshot_after_ms` so a single tool call returns the screen after the input.

### Tools

| Tool | Arguments | What it does |
|---|---|---|
| `status` | — | Console state (awake/standby), session, fps, age of the latest frame, firmware |
| `snapshot` | `max_width=1024`, `quality=80` | Current screen as a JPEG |
| `press` | `button`, `duration_ms=80`, `snapshot_after_ms?` | Tap a button |
| `hold` | `button`, `duration_ms` (≤ 10 000), `snapshot_after_ms?` | Hold a button, then release |
| `stick` | `side`, `x`, `y` (-1..1; y=-1 is up), `duration_ms`, `snapshot_after_ms?` | Deflect a stick, then re-center |
| `trigger` | `side`, `value` (0..1), `duration_ms`, `snapshot_after_ms?` | Analog L2/R2 |
| `sequence` | `steps[]`, `snapshot_after_ms?` | Several inputs in one call (press / hold / stick / trigger / wait) |
| `release_all` | — | Release every button, center the sticks, release the triggers |
| `wake` | — | Wake the console from rest mode |
| `standby` | `confirm="yes"` | Put the console into rest mode |

Buttons: `cross circle square triangle up down left right l1 r1 l2 r2 l3 r3 options create touchpad ps`.

A `sequence` example:

```json
[{"op": "press", "button": "down"}, {"op": "wait", "duration_ms": 400}, {"op": "press", "button": "cross"}]
```

### CLI

```
uv run ps5agent status                 # console + session state
uv run ps5agent snapshot -o screen.jpg # save the current screen
uv run ps5agent press cross            # tap a button
uv run ps5agent latency                # press-to-screen latency (start on the home screen)
uv run ps5agent daemon                 # run the daemon in the foreground with logs
uv run ps5agent stop                   # stop the daemon (releases all input first)
```

### Configuration

Environment variables. CLI flags of the same name override them.

| Variable | Default | Purpose |
|---|---|---|
| `PS5_HOST` | — (required) | Console IP |
| `PS5_USER` | first registered | PSN user for the session |
| `PS5_BACKEND` | `pyremoteplay` | `pyremoteplay`, or `fake` to run without a console |
| `PS5_RESOLUTION` / `PS5_FPS` | `720p` / `30` | Stream quality (`360p`…`1080p`, `30`/`60`) |
| `PS5_QUALITY` | `default` | Video bitrate: `very_low` … `very_high`. Lower it on weak Wi-Fi |
| `PS5_DAEMON_ADDR` | `127.0.0.1:9395` | Daemon API address (loopback only) |
| `PS5_MAX_HOLD_MS` | `10000` | Upper bound for any hold |

The daemon log is at `~/.config/ps5-agent/daemon.log`.

## Safety and reliability

- **No stuck input.** Everything is released on error, when the client disconnects, when the daemon stops, and when the session drops. A watchdog releases anything held past the cap. If the daemon is killed hard mid-hold, the console releases the input together with the session (verified on hardware).
- **One input queue.** Two agents cannot press at the same time.
- **Reconnects** with exponential backoff; `status` reports `reconnecting` honestly.
- **Stale and black frames are flagged.** A frame older than 2 s, or a black frame (content blocked from Remote Play), comes with a text warning.
- **Wi-Fi:** lost video packets are recovered with forward error correction (Cauchy Reed-Solomon). The network test measures the real MTU/RTT.
- The daemon API listens on `127.0.0.1` only.

## Measurements

PS5 firmware 26.06-14.10.00.03, Ethernet; host Windows 11, Python 3.12.

| Metric | Value |
|---|---|
| Press → visible change on screen | median **265 ms** (187–281 ms, 10 samples) |
| Stream | 1280×720 at ~30 fps |
| Video decoding | ~28% of one CPU core |
| Session connect | ~2 s |
| 3% of video packets dropped artificially | all losses recovered, 0 corrupt frames (46 in 12 s without FEC) |

## Known limitations

- One Remote Play session per console. If someone else is connected through Remote Play, the daemon waits until that session ends.
- After the daemon is killed, the console keeps the old session for ~30–40 s; the daemon reconnects by itself afterwards.
- The console sometimes ignores a press during a screen-transition animation. The agent sees that on the next screenshot and presses again.
- Analog L2/R2 and sticks are implemented per the protocol but not yet verified inside a game.
- No audio.
- Built on [pyremoteplay](https://github.com/ktnrg45/pyremoteplay) 0.7.6, which is no longer maintained. This project patches its incompatibilities with current firmware and libraries: its own FEC instead of `pyjerasure`, the protocol version in the network test, and pins `av<14` and `pyee<9`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `status` shows `connecting`, log says *Another Remote Play session is connected to host* | Another device (or a just-killed daemon) holds the session. Wait 30–40 s or disconnect the other device |
| `no PSN user registered with this console` | Run `uv run ps5agent --host <ip> register` |
| `wake` fails | Turn on **Enable Turning On PS5 from Network** in rest-mode features |
| Garbled picture on Wi-Fi | Set `PS5_QUALITY=low` or `PS5_RESOLUTION=540p` |
| Black screenshot with a warning | The game or app blocks Remote Play video |

## Development

```
uv run python tools/gate.py               # lint + format check + tests (one call)
PS5_HOST=<ip> uv run pytest -m hardware   # round trip against a real console
PS5_BACKEND=fake uv run ps5agent daemon   # run without a console
```

Tests run against a `FakeBackend`: frames come from a PNG folder or are synthetic, and `right`/`left` move focus. Hardware tests run only when `PS5_HOST` is set.

## Credits

- [pyremoteplay](https://github.com/ktnrg45/pyremoteplay) — the Remote Play protocol implementation used as the backend.
- [chiaki-ng](https://github.com/streetpea/chiaki-ng) — reference for protocol details; its FEC test vectors were used to validate the FEC implementation.
- [leolobato/ps5-mcp](https://github.com/leolobato/ps5-mcp) — inspiration for the tool set and the daemon + thin MCP architecture.

Not affiliated with Sony Interactive Entertainment. PlayStation and PS5 are trademarks of Sony Interactive Entertainment Inc.

## License

[MIT](LICENSE)
