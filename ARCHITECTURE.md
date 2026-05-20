# HIVE — Architecture

This document is the single source of truth for *how HIVE is built and
why*. It pairs a layered overview with the eleven Architecture
Decision Records (ADRs) that explain the choices a future contributor
will most often want to challenge.

> Audience: anyone reading the codebase for the first time. If you're
> a user looking for a quick start, read `README.md` instead.

---

## 1. Layered overview

```
┌────────────────────────────────────────────────────────────────────────┐
│  Desktop shell   Tauri 2 (Rust)            window + tray + sidecar     │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │  Frontend      React 18 + Vite + TS + Tailwind                 │    │
│  │  - Projects / Project view (chat + agents bar + composer)      │    │
│  │  - Automations / Skills / Plugins / Usage / Settings           │    │
│  │  - Global Ctrl+K search, audit-log viewer, safety dashboards   │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                  ↕  REST + WS  (CORS allowlist incl. tauri://)         │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │  Backend       FastAPI + uvicorn                                │    │
│  │  - Orchestrator  LangGraph state machine + SqliteSaver         │    │
│  │  - Workers       ClaudeCLIWorker / OllamaWorker / API fallback │    │
│  │  - Pipelines     APScheduler + webhook triggers                 │    │
│  │  - Telegram      aiogram v3 with allowlist gate                 │    │
│  │  - Registries    ClawHub / Cookbook / Smithery proxies (cached)│    │
│  │  - Security      command policy + secure_execute + audit log   │    │
│  │  - Safety        hard stops + circuit breakers + patterns      │    │
│  │  - Validation    evidence schema + validators + trust scores   │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                        │
│  Per-user state  ~/.hive/           SQLite + worktrees + credentials   │
└────────────────────────────────────────────────────────────────────────┘
```

Everything in the desktop shell is HIVE-specific Rust + a React frontend
that talks to the Python backend over `localhost:8765`. The backend
ships unchanged between web and desktop — Tauri is a transport, not a
runtime.

---

## 2. The seven invariants

These show up in every code-review comment. They're load-bearing
across the codebase; new contributions are evaluated against them.

1. **Worker abstraction**. The orchestrator never calls `claude` (or
   any other model) directly. It calls `Worker.run()` on an instance
   of `ClaudeCLIWorker`, `ClaudeAPIWorker`, or `OllamaWorker`. Adding
   a new backend means writing a new Worker class.

2. **Event sourcing**. Every state change is an event in SQLite. The
   UI tree, the agent cards, the cost log — every projection — can
   be rebuilt by replaying the event log.

3. **Git worktree per agent**. Each agent that touches files runs in
   `~/.hive/worktrees/<session>/<agent>/`. No two agents share a
   working directory. The Reviewer merges branches back.

4. **NDJSON streaming**. `stream_parser.py` buffers stdout, splits on
   `\n`, parses each line as JSON. No regex tricks against raw text;
   no shortcuts. A malformed line is dropped, not propagated.

5. **Approval correlation IDs**. Every `interrupt()` payload carries
   the `session_id`. Approval-decision callbacks resolve the same
   in-memory `asyncio.Future` whether the click came from the web UI,
   Telegram, or the CLI.

6. **Rate-limit signals are first-class**. `system/api_retry` events
   from claude flow into `HiveEvent.type = RATE_LIMIT`. The UI shows
   them; the safety stack (Phase 10) uses them to throttle.

7. **Cost discipline**. Opus is reserved for Orchestrator + Reviewer.
   Workers default to Sonnet. Local Ollama models handle non-reasoning
   tasks. Settings → AI exposes every choice; the picker warns when
   the Orchestrator is set to a model less capable than Opus, but it
   never blocks.

---

## 3. Architecture Decision Records (ADRs)

The narrative answer to "why is HIVE built this way instead of …".

### ADR-1 — Tauri over Electron

**Decision.** Ship the desktop shell as Tauri 2 (Rust + WebView2), not
Electron (Chromium + Node).

**Why.** Tauri's release bundle on Windows is ~10 MB vs. Electron's
~120 MB. The WebView2 host is already on every modern Windows install,
so Tauri doesn't ship a browser. The Rust runtime gives us a single
process to spawn + clean-kill the Python sidecar, system-tray API, and
window lifecycle without any of the IPC contortions Electron requires.

**Cost.** Two engineers on the team need to learn enough Rust to read
`src-tauri/src/lib.rs`. We've kept that file small (~270 lines) and
heavily commented for that reason.

### ADR-2 — LangGraph over CrewAI / AutoGen

**Decision.** Use LangGraph for the orchestrator state machine.

**Why.** LangGraph is the only mature option that combines
checkpointing (SqliteSaver), structured state, `interrupt()` for
human-in-the-loop, and conditional edges. Our session model is
explicitly multi-turn with parking points (`wait_for_user_node`);
without `interrupt()` we'd have to reimplement that ourselves.
CrewAI doesn't checkpoint. AutoGen is in maintenance mode.

**Cost.** LangGraph's API is younger than its competitors' — we pin
to a known-good version and read the changelog before bumps.

### ADR-3 — python-build-standalone over PyInstaller

**Decision.** Bundle a frozen Python via python-build-standalone for
the .msi/.dmg/.AppImage build path. Track the build in
`packaging/BUILD.md`.

**Why.** PyInstaller's frozen output trips Windows Defender's
heuristics often enough that we'd be fielding "Defender quarantined
HIVE" reports forever. python-build-standalone produces a real
CPython distribution that AV tools recognise.

**Cost.** Per-OS build. PyInstaller cross-compiled badly anyway, so
not a regression.

### ADR-4 — Git worktrees for isolation

**Decision.** Every agent runs in its own `git worktree`. Reviewer
merges via `git merge-tree`.

**Why.** Worktrees are the only sandboxing primitive that's
(1) standard, (2) understood by every developer, (3) supports
incremental merges. We tried per-agent tmpfs at one point — too
much state to capture for the merge step.

**Cost.** Worktrees need a real on-disk git repo. Cleaner installs
get one auto-initialised by `WorktreeManager.ensure_git_repo` (which
also self-heals missing global git identity per the Phase 9C bug fix).

### ADR-5 — Event sourcing in SQLite

**Decision.** Every state change is an append to `events` /
`cost_log` / `command_audit` / `pipeline_runs`. Projections (sessions,
agents, the per-project sidebar) are derived.

**Why.** Three things this gives us:

  - Replay-based recovery if the process is killed mid-run.
  - The audit log (`command_audit`) is literally the same shape as
    everything else — no separate logging system.
  - Cheap "ask the orchestrator about earlier work" history queries.

**Cost.** Two writes per event (event row + projection update). SQLite
WAL mode handles it fine for single-user load; we'd need a different
store if HIVE became multi-tenant (which we're explicitly not).

### ADR-6 — Opus for Orchestrator + Reviewer only

**Decision.** Default `orchestratorModel = claude:opus`,
`workerModel = claude:sonnet`. Settings → AI allows overrides; flipping
the Orchestrator to anything less than Opus surfaces an amber warning
but never blocks.

**Why.** Token economics. The Orchestrator decides architecture for
the session (one Opus call per turn). Workers do bulk work (many
Sonnet calls per turn). With Opus restricted to two roles, the cost
per session stays Sonnet-priced.

**Cost.** Users who want to pay more for marginally better worker
output need to know to flip the dropdown. We surface this in the
QuickStart chip ("Orchestrator: …") so the choice is visible per
session, not buried.

### ADR-7 — Subprocess `claude` CLI, not Anthropic SDK

**Decision.** `ClaudeCLIWorker` shells out to `claude -p "..."
--output-format stream-json` rather than calling the Anthropic SDK.

**Why.** Claude Max OAuth is only exposed via the CLI; the SDK
expects an `ANTHROPIC_API_KEY`. We want HIVE to ride a single Max
subscription per user, not require per-user API keys. `ClaudeAPIWorker`
exists as a fallback for the API-key path.

**Cost.** Stream parsing (`stream_parser.py`) — we own the NDJSON
parser. Worth it for the OAuth path.

### ADR-8 — Single-port FastAPI sidecar

**Decision.** The backend listens on **one** TCP port
(`localhost:8765`). Everything — REST, WebSocket, pipeline webhooks,
Telegram callbacks — lives behind the same FastAPI app.

**Why.** Two processes is a maintenance burden we can't justify for a
single-user app. The WebSocket + REST share the same auth surface
(CORS allowlist) and the same DB connection pool.

**Cost.** The CORS allowlist has to include every WebView origin
(`tauri://localhost`, `https://tauri.localhost`, `localhost:1420`,
`localhost:5173`). `tests/unit/test_cors.py` pins the list with a
regression test.

### ADR-9 — Two configs: dev + prod overlay

**Decision.** `tauri.conf.json` is the dev config (no `externalBin`).
`tauri.conf.prod.json` is the production overlay applied via
`tauri build --config …` and adds the `externalBin` slot pointing at
the frozen Python sidecar.

**Why.** Without this split, dev builds on machines that haven't run
PyInstaller fail because `tauri-build` resolves `externalBin` at
compile time and demands the file exists.

**Cost.** Two configs to keep in sync. They're 5 lines apart; the
runbook in `packaging/BUILD.md` documents the diff.

### ADR-10 — Command sandbox by classification

**Decision.** Every shell command an agent wants to run goes through
`secure_execute()` → `command_policy.classify_command()`. Three
buckets: `BLOCKED` / `ALLOWED` / `CONFIRMATION`. Five user-selectable
approval modes layer on top. Custom rules from
`~/.hive/custom_policies.json` can tighten or loosen specific patterns
— but the `BLOCKED` list is non-overridable, even in BLIND_AUTO.

**Why.** "Run everything an agent suggests" is a bad default for
autonomous loops; "ask before every command" is a bad UX. The
classification model gives us a single dial (the approval mode) that
maps cleanly to user mental models, and a hard floor for things no
agent should ever do (rm -rf /, sudo, credential reads).

**Cost.** A regex list is brittle by nature — we ship 209 parametrized
tests in `tests/unit/test_command_policy.py` precisely so changes to
the lists are visible to reviewers.

### ADR-11 — Per-worker circuit breakers + trust scores

**Decision.** Wrap every `_execute_worker` call in a per-worker-model
circuit breaker (`closed` → 3 failures → `open` for 5 min →
`half_open` probe). Record each completion's pass/fail to a
`worker_trust_scores` table; surface the scores in
`Settings → Trust profiles`.

**Why.** Some local Ollama models do good work some of the time. The
breaker stops a failing model from monopolising the work queue; the
trust score gives the user empirical data instead of folklore when
picking workers. Both are layer 2 of the safety stack (`backend/safety/`).

**Cost.** Two more pieces of state to think about. Both are pure
in-memory (breaker) or single-table (trust) — minimal infrastructure.

---

## 4. Module map

```
backend/
  api/                    FastAPI routers (one per surface)
    http.py               sessions, messages, approvals
    pipelines_http.py     CRUD + cron + webhook triggers
    registries_http.py    /api/registries/skills + /mcp + /diagnose
    security_http.py      policies, audit, approvals (Phase 10)
    safety_http.py        breakers, hard-stop defaults (Phase 10)
    validation_http.py    trust scores (Phase 10)
    cost_http.py / usage_http.py / detection_http.py / lifecycle_http.py
    ws.py                 /ws/{session_id} WebSocket
  orchestrator/
    graph.py              LangGraph nodes + run/resume/close
    state.py              GraphState TypedDict
    nodes/
      planner.py          orchestrate() → response + team
      spawner.py          worktrees + agent rows
      reviewer.py         merge + ReviewReport
    conflict_resolvers.py per-filetype heuristics (Phase 10)
  workers/
    base.py               Worker Protocol + HiveEvent
    claude_cli.py / claude_api.py / ollama.py
    stream_parser.py      NDJSON
  persistence/
    db.py                 schema, WAL, foreign-key enforcement
    events.py             write_event + projections
    recovery.py           startup crash detection
  worktrees/
    manager.py            git worktree create/remove/merge (self-heals git identity)
  skills/                 semantic skill registry (Phase 4)
  pipelines/              APScheduler + store (Phase 6)
  telegram/               aiogram bot + handlers (Phase 7)
  registries/             ClawHub + Cookbook + Smithery proxies
    cache.py              TtlCache
    curated.py            offline fallbacks (75 skills + 31 MCP)
    skills.py / mcp.py    live fetchers with per-source diagnostics
  security/               command sandbox (Phase 10 / Section 1)
    command_policy.py     classify_command()
    approval_mode.py      five modes + custom_policies.json
    executor.py           secure_execute() + audit
  safety/                 (Phase 10 / Section 6)
    hard_stops.py
    circuit_breaker.py    CLOSED/OPEN/HALF_OPEN per worker
    quality_monitor.py    rolling-average drop detector
    pattern_detector.py   stuck-state heuristics
  validation/             (Phase 10 / Section 5)
    schema.py             CompletionReport + Evidence
    validators.py         5 deterministic + Haiku stub
    trust.py              worker_trust_scores helpers

desktop/
  src/                    React frontend (Vite + Tauri WebView)
  src-tauri/              Rust shell (window, tray, sidecar)

cli/hive.py               typer CLI: run, start, sessions, skills,
                          pipelines, telegram, onboard

packaging/                python-build-standalone runbook, PyInstaller
                          spec, entrypoint, BUILD.md
```

---

## 5. Sequence diagrams

### 5.1 First turn — orchestrator chats back

```
User   →  POST /api/sessions {task, model, approval_mode, project_path}
                      ↓
                _session_runner spawned
                      ↓
              run_session(initial_state) → run graph
                      ↓
              ┌─────────────────────────────┐
              │  orchestrator_node           │
              │   - calls plan_team()        │
              │   - routes by has_active_team│
              └──────────┬──────────────────┘
                         ↓
                   "respond_node"    (chat only — no agents)
                         ↓
                   wait_for_user_node ─→ interrupt(awaiting_input)
                         ↓
              session paused, parked in checkpoint
              UI subscribes to /ws/{id} and streams events
```

### 5.2 Agent spawn with safety gates

```
User confirms approval card
        ↓
spawn_node:
  - check_hard_stops(concurrent_agents, tokens_used, …)
      → if violation: emit `safety_hard_stop`, abort
  - WorktreeManager.create() per agent
        ↓
run_workers_node — asyncio.gather under Semaphore(MAX_CONCURRENT)
        ↓
for each agent:
  - breaker.can_attempt()?
      → no: emit `safety_breaker_open`, return failed AgentResult
  - claude/ollama Worker.run(prompt, config)
      → stream NDJSON events → write_event → ws.emit
  - on completion:
      - _auto_commit_worktree (self-heals git identity)
      - breaker.record_success()|record_failure()
      - record_trust_completion(worker_id, passed)
        ↓
review_node:
  - WorktreeManager.merge_to_main per branch
  - if merge conflict: conflict_resolvers.resolve_conflict()
      → if heuristic fails: escalate to LLM (Phase 11) or user modal
```

### 5.3 Sandbox approval round-trip

```
agent decides to run `npm install left-pad`
        ↓
secure_execute(cmd, mode=SMART_AUTO, …)
  - classify_command → CONFIRMATION
  - should_execute → "ask"
  - mint token, return ExecuteResult(status='pending_approval')
        ↓
HTTP emits `command_approval_requested` to WS
        ↓
React modal opens with the command + matched_pattern + rationale
        ↓
POST /api/security/approvals/{token} {approved: true}
        ↓
resume_with_approval(token, approved=True)
  - asyncio subprocess.shell(cmd) → capture stdout/stderr
  - _audit(classification='confirmed', user_approved=1)
  - return ExecuteResult(status='completed', exit_code, stdout, stderr)
```

---

## 6. Where to look next

- **First-time user**: `README.md`.
- **First-time contributor**: this file → then read
  `backend/orchestrator/graph.py` end-to-end.
- **Adding a new MCP server**: `backend/registries/curated.py`.
- **Adding a new approval mode or rule**: `backend/security/approval_mode.py`
  + `backend/security/command_policy.py`.
- **Adding a new Worker backend**: `backend/workers/base.py` (Protocol).
- **Looking for the test for X**: `grep -r "def test_" tests/`.
