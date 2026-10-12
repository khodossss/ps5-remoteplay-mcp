# PS5 Remote Play MCP
MCP server + daemon: an LLM agent (Claude Code, Codex) sees the screen of a stock PS5 and drives it like a gamepad over Remote Play on the LAN.

## What this project is
- **Nature**: not yet agreed — until the owner says otherwise, full production discipline applies.
- **Graph**: `@t6ylm7b6cpmacd2lohhdpkka/ps5-agent-bridge` (`r853`) — every session starts with `iskron_orient` here. The graph is private to the owner; external contributors skip every graph step in this file.
- **Focus holon**: #1 «🎮 Мост агента к PS5».
- **Agent role**: #3 «🤖 Агент-разработчик моста» — adhikarin, steward of the focus holon. Your inbox: `iskron_orient(focus="3")` at session start.
- **Owner role**: #2 «👤 Владелец моста к PS5» (svatantra 主) — out-of-mandate questions go here as `posed_to` vimarshas.
- **Stack**: Python ≥3.12, uv, MCP Python SDK 2.x (`MCPServer`, stdio), aiohttp (daemon API), httpx (client), vendored + patched pyremoteplay 0.7.6 (GPL-3.0), pywebview (sign-in window); fallback backend chiaki-ng not implemented; dev host — Windows 11. Shipped to users as a PyPI package run by `uvx ps5-remoteplay-mcp`. License GPL-3.0-only.
- **Production statement**: not yet agreed — run the interview (say iskronify) before relying on a cost of failure. Fixed by the owner's handoff: under no failure may a button stay pressed; Remote Play credentials never appear in logs or tool responses; the daemon API listens on 127.0.0.1 only.

## Persistence rules
State lives in the **repo** or in the **graph** — nowhere else. The harness's built-in memory (whatever it is called — per-project memory dir, conversation summaries, `/tmp`, machine-local files) is **forbidden entirely, not by category**: nothing goes there — no project fact, no user preference, no working-style note. (why: local memory is invisible to every other agent and machine, so it drifts silently and breaks the reproducibility that makes a second machine or agent possible.)
- **Repo**: code, configs, conventions, code gotchas, branch state — the artifact itself.
- **Graph**: methodology, design decisions, open questions (vimarshas), plans, handoffs, lessons, hints — the thinking around it. Do not retell graph content in the repo; link the vimarsha or holon.
- **Fetch state; never reconstruct it from memory.** "We decided…" without a source? Stop and read the graph or repo before acting.
- **External design/spec files are drafts to intake**, not the record: the graph holds decisions; such a file is a view of them.
- **There are three named parking spots.** The undisciplined surface is free, the disciplined one costs, and under pressure laziness always finds a parking spot: the prose of this file; a sinn phenomenon for something that acts; a lone `context` arrow that silences the detector. Finish line for a write: **a node is not written until you name what pulls it** — which kriya breaks if the node disappears? None — it is parking, not a record. Read the fork BEFORE writing, not from a warning after (that only chronicles): a door enters the graph not as a thing nor as the meaning "door", but as **the place where a doer acts** — upadhi of the kriya that passes through it.
- **This overrides the harness's own memory instruction**, which invites a `project` category and will keep inviting — the pull is strongest exactly when something feels worth saving and this file is long out of context. Route instead, always — and **before finishing, check that every durable fact from the user's context is persisted by this routing: an unpersisted fact is a task failure, not a nicety** — asking **whose fact is this?**
  Repo convention, code fact, this project's procedures, **its servers and deploy pipeline, its dated obligations** → this file / docs / code, or a node in this repo's graph; work state, decision, open question → a vimarsha in the graph.
  A dated obligation (renewal, deadline) is a node carrying the date in `attrs`: a mention in one README loses it. Project rules never land in another project's or the user's graph.
  A fact that is **the user's own** and serves no single project — their machines, deadlines, people, cross-project lessons → their personal graph `@t6ylm7b6cpmacd2lohhdpkka/mind` (**minding**), written when learned, not at session end; a fact about **another** project → that project's graph. Standing preferences ("how to work with me") are instructions, not facts: project-scoped → this AGENTS.md, cross-project → the user's global instructions file; never the graph or the memory dir.
  The memory dir keeps no residue — "it's just a preference" is how the category comes back.
- The local memory dir is **evacuated and frozen**: `MEMORY.md` holds a one-line prohibition stub pointing here, and a `PreToolUse` memory-guard blocks writes there (exit 2). Whatever accumulated moves to its real home before freezing — nothing is deleted unread.

## Session lifecycle
Graph = the work (structure, open questions, what's next). Git = how we got here (SHAs, branches, PRs). **Git references never enter the graph** — no SHAs, branch names, PR numbers or "shipped/merged" in nodes (they rot on rebase).
- **Session start:** enter via the `entry` skill — it ends in signs, not reading; here only the addresses: graph, focus holon, agent role and owner role from "What this project is". Address the owner by their role's seq, not the `me` sentinel.
- **Starting work: graph first, then project, then code.** A substantive task enters in three beats: (1) **graph reconnaissance** — what is recorded about the place of change, which vimarshas are open, what was decided and rejected, **and what is recorded about external surfaces the work will touch** (see "External surfaces"); driven by `entry`; (2) **integration field** — from the focus holon, steward role and the changed nodes, derive consumers, effects, neighbouring holons and their doers via `integrity`; walk relays with `lens="trace"`, design missing kriyas and phenomena, stitch gaps via `weaving`; (3) **design** the change (`design`) — only then code. Skipping gets costlier left to right. The one exception is **explicit**: the human said "just work" or named another protocol — then go to code and pay the reconnaissance debt at the reconcile beat at task end. Silence is not "just work".
- **A decision is recorded when made, not when executed.** Wherever it comes from — chat, a socket message, two agents agreeing — it stands in the graph **immediately**, with the modes it really has now: epistemic no higher than `anumita`, ontic `anagata`, volitive `chanda`/`adhimoksha`. Record who decided and what counts as done. A changed situation is reflected just as immediately. (why: a decision left in the conversation dies with it; a late record is what you remember as decided, which is not the same.)
- **Every task is described before it starts — and recorded as what it is.** Before the first change outside the graph the work stands in the graph: a one-off deed as an anga vimarsha on the transformation it moves (a large one — its own bianhua), with before/after in its body; **a kriya only for a repeatable transition** whose every run eats the same ahara and produces the same utpatti. Test: ask the "kriya" what it eats and produces on its *next* run — no answer means it is a task. While work runs, the graph moves with it. On **merge** (not push) modes switch to what the merge made true. Then **reality-audit**: check the claim against the deployed artifact, not the diff.
- **Every merge → update the graph.** A push that only opened or updated a PR shipped nothing. The post-merge sequence hangs on the merge **event**, never on a lull. When merged, every step below is mandatory:
  - **Reconcile with reality.** Record what positions the change in the target system: architecture, module APIs, delivery, user experience, integration. Pure repo mechanics stay in git. **Updating the graph means weaving, not editing prose.** Zero nodes and zero arrows after a substantive wave is an unfinished step — say so plainly if there truly was nothing, and why.
  - **Advance the map.** Keep open work tied by `anga` to the transformation it moves. A thin `genre=hint` seed only for what the graph cannot carry.
  - **Close by axis, not by a feeling of "done".** Record the answer as `addressed_by` to the node carrying it; release (`visarjana`) is a separate volitional act. Release yourself when three things agree: the answer is a node in the graph; the repo shows it; reality shows it as far as reachable — where unreachable, the user's word stands in and you asked for it. Otherwise prepare the release and present it to the owner.
  - **Sweep the shipped holon.** A push implementing designed nodes switches their modes (anagata→vartamana, kalpita→pratyakshita) across the *whole* designed holon, and closes the design vimarshas it resolved.
  - **Work the inbox.** `posed_to` questions the work answered close by the rule above; stale ones are parked or grouped.
  - **Reconcile code and graph.** End of every substantive task is the `reconcile` beat: nodes vs code, code vs graph, discarded and rejected alternatives recorded and referenceable. Remaining debts as vimarshas, not a story.
  - **Vocabulary pass.** Reread what you are about to land for borrowed project-management words (ticket, backlog, sprint, epic, story, done, blocker, committed). Do **not** substitute them yourself: name each to the user and ask what the project calls it.

  `weaving` / `design` carry the *how*.
- **Design completeness:** a design is not *done* until its decisions, risks and lifecycle are in the graph. A design/spec file written by another suite is a draft view: intake it **in the same session**. Working autonomously: land decisions and risks now; propose a bianhua with its telos marked for owner confirmation.
- **Execution suites run execution.** Planning, TDD, debugging, verification, review belong to the installed execution suite; the graph carries only the memory/design plane. Decisions and risks born in execution still land in the graph **before session end**.
- **A claim you made is not a claim you accept.** Behavioural claims are closed by the verdict of a cold `verifier` subagent, never by your rereading. Give it the claim, the carrier and the falsifier from *Reality* and **wait for the verdict**. Where the repo has no verifier role, observe the carrier yourself; never close a behavioural claim from the source that should have produced it.
- **Hook merging.** Where the harness has a hooks file, entries of different suites coexist — add alongside, never overwrite.
- These reminders are automated where the harness can: session-start hook, post-`git push` hook, memory-write hook. Claude Code: `.claude/settings.json` — not wired yet (deferred by quick mode).
- **Keep this file honest.** It is generated by `iskronify` and stamped at the bottom with its contract. Rerun `iskronify` when the installed skill announces a newer contract, or when the sources it derives from moved after the stamp: `git log -1 --format=%cd -- pyproject.toml uv.lock .github/` against the stamp date. The installed contract number is the first word of the `iskronify` skill description. (why: a stale AGENTS.md is read with full confidence every session.)
- **Keep your toolchain fresh.** Updates are **on by default**: take them as the channel delivers, do not pin.

### Stage self-check
Gate green and a coherent stage finished — PR opened or updated, or you are about to touch nodes beyond those you started with — reread your branch diff against trunk for: bugs, fragile spots, weak error handling, DRY/SOLID violations, repeated patterns, missing or useless tests, files over 150 lines and god units mixing many concerns. Fix in the **same branch** and push again — or say plainly nothing surfaced. Do not invent findings. **Per stage, not only at the end.**

### Cold stage review
**Self-check does not replace a cold review.** After the self-check of an opened/updated PR or a finished large stage, **open a review by a top-tier subagent** (`reviewer` role) where the harness can. If it cannot, say plainly there was no cold review. **Only the push has a watchman.**

The reviewer's field is four things, all mandatory:
- **branch diff against trunk** — the whole branch, not the last commit;
- **the repository itself** — so it can open neighbouring code;
- **the focus holon and its steward role** from "What this project is";
- **references to graph nodes the diff touches** — not a retelling of them.

The reviewer runs `integrity` read-only from the given nodes and, besides code findings, returns an **integration report**: what is affected, which relays pass or break, whether neighbours are ready by available evidence, open questions, whom to wake via `collaborate`. Unknown stays `unknown`. Missing references or a broken trace → `NEEDS_CONTEXT` naming the gap: a graph defect. Fix what comes back in the same branch; reject a finding only with a recorded "why".

### Branch discipline
One branch until its merge — commit follow-ups into it. After merge, clean up locally:
1. `git checkout main && git pull`.
2. Delete the merged branch (`git branch -d <name>`); prune others already in `main`.
3. Update the graph: weave the shipped state into the holon, close what the merge resolved (`weaving`).
4. Confirm the cleanup before the next task.

## Working principles
1. **Think before coding.** Name assumptions; when unsure, ask — naming *what exactly* is unclear. **Ask the human in text**; never use an interactive option-picker. Surface competing readings; push back on a simpler path or a false premise. Check repo + graph before writing; fetch, don't recall. Touch the live system before trusting a type, name or doc. Out-of-bounds questions become vimarshas `posed_to` the owner's 主 role.
2. **Simplicity first.** Minimum code for the task. No speculative features, no abstractions for one-off code, no handling of impossible errors. Validate at boundaries; trust internal invariants.
3. **Stay inside the repo boundary.** A change belonging to another holon is recorded as a vimarsha on that holon's node in its graph.
4. **A second implementation is an event to report.** About to write what already exists? Derive both places via `integrity`, name them to the user, and propose reunion or a named deliberate fork.
5. **Surgical changes.** Touch only what the task needs. Keep existing style; the linter is authoritative. Delete only dead code your change created.
6. **Goal-driven execution.** Tasks → verifiable goals. Bugs: pin with a failing test before the patch. Multi-step: plan as `step → check` pairs. Runtimes: verify in the real environment — *Reality* names this project's carriers. Name the falsifier before looking and observe the carrier, not its source.
7. **Read before answering an open question.** Discuss / think through / investigate / design / plan / analyse — answer from recorded thinking, not training data: query the graph several ways first.
8. **Think in the graph, speak the project's language.** The graph's structural vocabulary is for reasoning; it does not appear in what you say to the user until they use it first. Talk *about* work in plain words: question, change, what is open, what it unblocks.

## Integration field — from the graph only
The focus holon and steward role are the only permanent root of traversal. AGENTS.md keeps **no list** of shared surfaces, consumers or dependencies, and does not ask the human for one. For every change, name the graph nodes whose embodiment is in the diff and run `integrity`; trace phenomena both ways with `iskron_orient(lens="trace")`. A dependency the traversal misses is a model defect: design the missing kriyas and phenomena, stitch edges via `weaving`.

## External surfaces — what you use and do not own
Someone else's API, SDK, CLI, protocol. Agents **guess** them: memory from training is indistinguishable from knowledge from the inside, and a field that does not exist looks in code exactly like one that does.
- **Before working, record the part of the surface you will touch** as a graph node, with the version you looked at.
- **Source by seniority:** your own observation (calling it, `--help` of the installed binary, types of the installed package) beats docs; docs beat memory; **memory is not a source at all**. Write epistemics honestly: `pratyakshita` only for what you observed.
- **Weave the link:** the surface node is `upadhi` of the kriya acting through it.
- **Keep in step:** found a mismatch or a version bump — fix the node in the same move.
- **The reference works both ways:** source touching an external surface carries `(graph …, node #N)` — and you **read that node before working**.

## Reality — what claims are checked against
Not fully settled: run the interview (say iskronify). Seeded by use:

| Claim class | Canonical carrier | How to observe | Who can |
|---|---|---|---|
| Session / input / frames work | a live PS5 on the host's LAN, through the daemon | `PS5_HOST=<IP> uv run pytest -m hardware`; `uv run ps5agent snapshot` and look at the image | agent (console on the host LAN) |
| An agent drives the console over MCP | the MCP server over stdio, launched as Claude Code launches it | stdio client `mcp.Client(StdioServerParameters(uv run ps5-remoteplay-mcp))` → `call_tool` | agent |
| Users can install it | the built wheel in a clean `uvx --isolated` env (py3.12–3.14) | `uv build`; `uvx --isolated --python 3.13 --from dist/<wheel> ps5-remoteplay-mcp --help` | agent |
| Sign-in window links a console | pywebview window + a real PSN account + PIN on the TV | `uv run ps5agent setup --force` | user (PSN password, TV) |
| Latency | a live PS5 on the home screen | `uv run ps5agent latency` | agent |

**Ceiling**: behaviour inside a running game (analog L2/R2, sticks) needs a game running and the owner's go-ahead; Wi-Fi loss was verified only by artificial packet drops.

## Graph ↔ repo: where things live
| Concern | Repo | Graph |
|---|---|---|
| Code, configs, lockfiles | ✓ | |
| Commands, conventions, gotchas, stack | ✓ (AGENTS.md) | |
| Branch state, what is in flight | git + PR body | ✓ (`genre=hint` — work without a PR) |
| Methodology, ontology | | ✓ |
| Design decisions, open questions | | ✓ (vimarshas) |
| Plans, task lists, session handoffs | | ✓ |
| Lessons | | ✓ (graph first; thin `genre=hint` for the off-map remainder) |
| Commit history, PRs, SHAs | git | (never in the graph) |

**No `HANDOVER.md` — a decision, not an omission.** Branch and in-flight state: `git branch` / `log` and the open PR; how claims are checked: *Reality*; why and what is open: the graph; work without a PR yet: a `genre=hint` seed. Branch state is told **in the PR body**.

## Commands
| What | Command |
|---|---|
| Gate (ruff check + format --check + pytest) | `uv run python tools/gate.py` |
| Hardware test (needs the console) | `PS5_HOST=<IP> uv run pytest -m hardware` |
| Daemon in the foreground | `PS5_HOST=<IP> uv run ps5agent daemon` |
| Status / snapshot / press / latency | `uv run ps5agent status \| snapshot -o f.jpg \| press cross \| latency` |
| Stop the daemon | `uv run ps5agent stop` |
| Link to the console (sign-in window + PIN) | `uv run ps5agent setup` (`--no-gui` for terminal) |
| MCP server (agents launch it; no args = MCP, args = CLI) | `uv run ps5-remoteplay-mcp` |
| Build / try as users get it | `uv build` → `uvx --isolated --from dist/<wheel> ps5-remoteplay-mcp --help` |
| Publish (owner only, needs a PyPI token) | `uv publish` |
| Format | `uv run ruff format src tests tools/gate.py` |

## Project structure
- `src/ps5agent/vendor/pyremoteplay/` — vendored protocol library, patched in place; every change listed in its `NOTICE.md`; excluded from ruff.
- `src/ps5agent/backend/` — `ConsoleBackend` (base), `remoteplay.py` (session over the vendored library), `fake.py` (tests, `PS5_BACKEND=fake`).
- `src/ps5agent/console.py` — discovery, saved console (`console.json`), linking (PSN redirect → user, PIN → registration).
- `src/ps5agent/login_window.py` — pywebview window: Sony sign-in, catches the redirect, PIN form.
- `src/ps5agent/engine.py` — input: serialization, watchdog, release guarantees.
- `src/ps5agent/daemon.py` — session, reconnect, loopback HTTP API.
- `src/ps5agent/client.py` — daemon client + autostart; `mcp_server.py` — tools; `cli.py` — `ps5agent`.
- `src/ps5agent/fec.py` — `pyjerasure` replacement (Cauchy Reed-Solomon, GF(2^8)).
- `tests/` — unit + MCP contract tests on fake; `test_hardware.py` — only with `PS5_HOST`.
- `tools/gate.py` — the gate; `tools/spike.py` — one-off phase-0 spike (excluded from lint).

## Code conventions
- **Meaning lives in the graph, code references it.** A comment carrying the rationale of a decision, discarded alternatives or integration design is a graph node living away from home: move the meaning to the graph and leave a reference in code — "(graph `ps5-agent-bridge`, node #N)". Boundary: **the mechanics of a step — in words in the comment; meaning, rationale, integration — in the graph.** The reference works both ways: after referencing, check the node really says it. This repo is public: external contributors have no graph access, so a graph reference never replaces the one-line "why" a reader needs to change the code safely.
- **External APIs (pyremoteplay, chiaki-ng, MCP SDK)**: class and method names come from the installed package and its source, never from memory.
- **Test discipline**: unit tests on `FakeBackend`, an MCP contract test per tool, hardware tests `@pytest.mark.hardware` (only with `PS5_HOST`). Check the "nothing held" invariant (`FakeBackend.idle`) in every test of a new input path.
- **Gotchas**:
  - pyremoteplay is vendored, not a dependency: the PyPI release pulls `netifaces` (no Windows wheels — breaks `uvx`) and pins old `pyee`/`av`. Fix protocol bugs in `src/ps5agent/vendor/pyremoteplay` directly and add a line to its `NOTICE.md` (GPL requires marking changes). Run the hardware test after any change there.
  - The vendored library's DEBUG logs print the handshake key and ECDH secret — `console.prepare` keeps logger `ps5agent.vendor.pyremoteplay` at INFO or above. Call `console.prepare(cfg)` before using the library (it also points profiles at `~/.config/ps5-agent/profiles.json`).
  - Audio is disabled in the receiver (decoder and resampler are skipped).
  - Sony redirects only to `remoteplay.dl.playstation.net` after sign-in: a normal browser cannot hand the code back. The pywebview window catches it from its own navigation. pywebview needs the main thread of its own process: the MCP `setup` tool spawns `ps5agent setup --gui-only` with stdin=DEVNULL; exit code 3 = no GUI → the tool returns the chat fallback (link → `setup(redirect_url)` → `setup(pin)`).
  - `[WinError 10048]` on DDP port 9303 in the log is harmless: two processes discovering at once; the library falls back to another port.
  - Windows: after a pyremoteplay session, closing the asyncio loop hangs on a UDP operation — processes holding a session exit via `os._exit` after cleanup (`daemon.run`).
  - The vendored library sends UDP from other threads (controller worker, AV executor); asyncio transports are loop-thread only, and a cross-thread send kills the stream on Windows (`Fatal write error on datagram transport`). `RPStream.Protocol.sendto` marshals to the loop — any new send path must go through it.
  - Two `python.exe` per daemon or MCP server on Windows is one process: the venv `python.exe` is a launcher that starts the real interpreter as a child. The daemon port (9395) already makes a second daemon fail to bind.
  - The daemon is spawned with `CREATE_BREAKAWAY_FROM_JOB`: MCP clients on Windows kill the server's process tree on exit. Without the flag the daemon dies with every MCP server.
  - aiohttp cancels a handler when the client leaves only with `handler_cancellation=True` (`daemon.make_runner`) — otherwise a hold runs to its end.
  - A killed daemon leaves a zombie session on the console for ~30–40 s: "Another Remote Play session is connected to host". The daemon reconnects by itself on backoff.
  - mcp 2.x: `MCPServer` instead of `FastMCP`; result fields are snake_case (`is_error`, `mime_type`); errors meant for the model are `ToolError` (otherwise the text is masked); enter and exit an in-process `Client` in the test body, not in an async fixture (anyio cancel scope).
  - The Create button is `SHARE` in pyremoteplay.
  - The console swallows presses during screen-transition animations — not an input bug.

## What to update when
- `AGENTS.md` — inverted default: **if it can be learned by reading a graph node, it is not here.** This file holds only what is needed BEFORE the agent reaches the graph: commands, the entry into orientation, code invariants the linter cannot express, forks that must stop you before acting — and is updated when THAT changes. Cleaning up written prose is a reconcile beat with transfer into carrying nodes, never deletion.
- `CLAUDE.md` — regenerate the copy on every change to `AGENTS.md`; it is a duplicate, never edited directly.
- The project graph — every push (see "Session lifecycle").

## Git workflow
- **Conventional commits** (`feat:`/`fix:`/`chore:`/`refactor:`/`docs:`/`test:`). Branches `feat/…`, `fix/…`, `chore/…`. PR titles in the same format.
- **No AI attribution**: no `Co-Authored-By` trailers for Claude or other agents in commits, no "Generated with" lines in PR bodies — the repository lists the owner as its only contributor (owner's decision).
- **Forge**: GitHub, `khodossss/ps5-remoteplay-mcp` (public); CLI `gh` — observe with `gh pr list`, `gh pr checks <n>`.
- **Local gate — one call**: `uv run python tools/gate.py`; call it by name, never assemble the steps by hand. No CI yet.
- **Definition of done**: not yet agreed.
- **Never** `--no-verify`, `--force`, `--no-gpg-sign`, `git reset --hard` without an explicit instruction from the user.

*(iskronify: contract `6`, stamp `2026-10-12` — rerun when the installed iskronify description names a
higher contract or when the sources this file is derived from moved after this date.)*
