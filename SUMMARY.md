# HIVE — Phase 5 Complete: Web UI + WebSocket

**Date:** 2026-05-15
**Phase:** 5 — Web UI + WebSocket
**Status:** ✅ All done

---

## What Was Built

| Component | Description |
|-----------|-------------|
| `backend/api/event_bus.py` | Per-session `asyncio.Queue` — `get_or_create`, `emit`, `remove`. Drop-on-full, zero dependencies |
| `backend/api/schemas.py` | Pydantic models: `CreateSessionRequest/Response`, `ApproveRequest`, `SessionInfo`, `AgentInfo` |
| `backend/api/http.py` | REST router: `POST /api/sessions`, `GET /api/sessions[/{id}]`, `POST /{id}/approve`, `POST /{id}/message`. Background `_session_runner` handles interrupt futures |
| `backend/api/ws.py` | WebSocket `/ws/{session_id}` — streams queue events with 20s keepalive ping |
| `backend/main.py` | FastAPI app with lifespan, CORS for `localhost:5173`, includes both routers |
| `backend/orchestrator/graph.py` | Added `_emit_to_ws()` helper + emit calls in `plan_node`, `spawn_node`, `_execute_worker` |
| `cli/hive.py` | Added `hive start [--port 8765] [--reload]` command |
| `frontend/` | Full Vite + React 18 + TypeScript + TailwindCSS + @xyflow/react + Zustand app |
| `tests/unit/test_phase5.py` | 15 tests: event_bus, REST endpoints, WebSocket |
| `~/.hive/skills/` | 8 real SKILL.md files: python-testing, async-python, fastapi-patterns, react-hooks, git-workflow, sql-schema, typescript-strict, debugging-async |

## Architecture: Per-Session Message Queues (Invariant 6)

Each session gets its own `asyncio.Queue` at the moment `POST /api/sessions` is called. The background `_session_runner` task drives the LangGraph graph and emits events into this queue. The WebSocket handler for `/ws/{session_id}` reads from the same queue and forwards to the browser in real time.

Approval interrupts: when `run_session` returns `SessionInterrupt`, `_session_runner` emits `{"type": "interrupt", ...}` to the queue (browser shows ApprovalModal), then suspends on an `asyncio.Future`. `POST /{id}/approve` resolves the future, and the graph continues.

## Frontend

- **Dashboard** — task input (⌘↵ to submit), model + approval-mode selectors, session cards grid
- **TabBar** — all sessions as tabs with live status dots
- **TreeCanvas** — @xyflow/react + dagre auto-layout. Orchestrator (🐝 spinning ring) → agent hexagons. Edges animate when agent is running. Click to inspect in sidebar.
- **AgentNode** — role emoji, current activity truncated, status ring color, progress shimmer when running
- **AgentSidebar** — token counters, live output log (last 500 chunks), status
- **EventLog** — structured log strip at bottom, auto-scrolls
- **ApprovalModal** — team list, confidence bar, ✓ Approve / ✗ Reject
- **InputSection** — urgency mode buttons (💬 ✏️ ⛔ ⚡), message input, shown only when session is active

## How to Run

```bash
# Terminal 1 — backend
cd ~/hive && hive start

# Terminal 2 — frontend dev server
cd ~/hive/frontend && . ~/.nvm/nvm.sh && npm run dev
# → http://localhost:5173
```

## Test Results

```
87 passed in 10.03s
```

(72 previous + 15 new Phase 5 tests — 0 regressions)

Frontend build: `npm run build` → 497 modules, 440 KB JS, 32 KB CSS, 0 TypeScript errors.

---

*Previous phase summaries preserved below.*

---

# HIVE — Phase 4 Complete: Skills Registry + Semantic Injection

**Date:** 2026-05-13
**Phase:** 4 — Skills Registry
**Status:** ✅ All done

---

## What Was Built

| File | Description |
|------|-------------|
| `backend/skills/embedder.py` | `sentence-transformers/all-MiniLM-L6-v2` wrapper — lazy load, serialize/deserialize, cosine similarity |
| `backend/skills/registry.py` | CRUD + semantic search — `import_skill`, `create_skill_file`, `list_skills`, `search` |
| `backend/skills/injector.py` | Builds `## Relevant Skills` markdown block for agent system prompts |
| `backend/persistence/db.py` | Added `skills` table (id, name, description, tags, path, instructions, embedding, version) |
| `backend/orchestrator/graph.py` | `_execute_worker` now searches top-3 skills and injects them via `WorkerConfig.system_prompt` |
| `cli/hive.py` | Added `hive skills list/import/create/test` subcommand group |
| `tests/unit/test_phase4.py` | 15 tests: embedder, registry CRUD, semantic search, injector, graph injection |

## Skills Format

```
~/.hive/skills/<name>/SKILL.md

---
name: python-testing
description: Write pytest tests for Python code
tags: ["python", "testing"]
version: 1
---

## Instructions
Use fixtures, parametrize, aim for 80%+ coverage.
```

## CLI

```
$ hive skills create "python-testing" --description "Write pytest tests" --tags "python,testing"
Created: /home/user/.hive/skills/python-testing/SKILL.md
Edit the file, then run: hive skills import /home/user/.hive/skills/python-testing/SKILL.md

$ hive skills import ~/.hive/skills/python-testing/SKILL.md
Importing ... Imported skill 'python-testing': Write pytest tests

$ hive skills test "write unit tests for my API"
Searching for: 'write unit tests for my API'  (top_k=5, threshold=0.30)
1. [python-testing] python-testing
   Write pytest tests for Python code
   tags: python, testing
```

## How Injection Works

1. Before each agent starts, `_execute_worker` calls `search_skills(role_task, top_k=3)`
2. Top-K skills above cosine similarity threshold (0.3) are returned
3. `build_skill_context(skills)` produces a `## Relevant Skills` markdown block
4. Block is passed as `WorkerConfig.system_prompt` → prepended to the agent's prompt
5. If no skills match or registry is empty → no injection, no side-effects

## Test Results

```
72 passed in 46.30s
```

Note: 46s is the first-time HuggingFace model download (90MB). Subsequent runs are ~1s.

---

*Previous phase summaries preserved below.*

---

# HIVE — Phase 3 Complete: Approval Modes + Confidence Escalation

**Date:** 2026-05-13
**Phase:** 3 — Approval Modes
**Status:** ✅ All done

---

## What Was Built

| Component | Description |
|-----------|-------------|
| `approval_node` in `graph.py` | LangGraph node that calls `interrupt()` when approval is needed |
| `SessionInterrupt` dataclass | Returned by `run_session`/`resume_session` when graph is paused |
| `resume_session_with_value()` | Resumes an interrupted session with `Command(resume={"approved": ...})` |
| Updated `resume_session()` | Now surfaces `SessionInterrupt` when session is paused at approval gate |
| `--approval-mode` CLI flag | `full-auto` (default) \| `checkpoint` \| `manual` |
| Approval UI in CLI | Box display of proposed team, confidence, rationale — `typer.confirm()` prompt |
| `GraphState.approval_mode` | Persisted in LangGraph checkpoint — survives process restarts |
| `tests/unit/test_phase3.py` | 9 tests covering all approval paths |

## Approval Logic

| Condition | Behavior |
|-----------|----------|
| `full-auto` + confidence ≥ 0.7 | Runs without any prompt |
| `full-auto` + confidence < 0.7 | Pauses and shows approval UI |
| `checkpoint` or `manual` | Always pauses, regardless of confidence |
| User rejects | `abort_node` runs → returns `AgentResult(status="cancelled")` |
| User approves | Continues to `spawn_node` → agents run normally |

## CLI Example

```
$ hive run "Build a REST API" --approval-mode checkpoint

Session: a3f2c1d4  |  Backend: claude:sonnet  |  Approval: checkpoint
Task: Build a REST API
────────────────────────────────────────────────────────────

┌─ HIVE: Approval Required ──────────────────────────────────────────
│  Checkpoint: review team composition before agents launch.
│
│  Proposed team:
│    Thinker      x1  [claude:sonnet]
│    Builder      x2  [claude:sonnet]
│    Tester       x1  [claude:sonnet]
│    Debugger     x1  [claude:sonnet] (passive)
│
│  Confidence: 88%  |  Rationale: standard dev team for REST API
└────────────────────────────────────────────────────────────────────

Approve this team and proceed? [Y/n]:
```

## Test Results

```
57 passed in 0.99s
```

All 57 tests green (48 from Phase 0–2, 9 new Phase 3 tests).

## Key Implementation Detail

`interrupt()` is called inside `approval_node`. LangGraph checkpoints the state *before* the interrupt. When `resume_session_with_value()` is called with `Command(resume=...)`, LangGraph re-runs `approval_node` — on the second run, `interrupt()` returns the resume value directly instead of pausing. The node then reads `response["approved"]` and either sets `approval_rejected=True` or returns `{}`.

This means `hive resume <session-id>` works even after a process restart — the state is in SQLite.

---

*Previous phase summary preserved below.*

---

# HIVE — Phase 2 Complete: Multi-Agent Orchestration

**Date:** 2026-05-13
**Phase:** 2 — Multi-Agent Orchestration
**Status:** ✅ All done

---

## What Was Built

| File | Description |
|------|-------------|
| `backend/worktrees/manager.py` | Git worktree manager — create/remove per-agent branches, merge-to-main, conflict detection |
| `backend/orchestrator/nodes/planner.py` | Planner node — calls `claude:sonnet`, returns `TeamComposition` JSON with brace-depth extraction |
| `backend/orchestrator/nodes/spawner.py` | Spawner node — expands team into agents, creates worktrees concurrently (Semaphore 3), registers in DB |
| `backend/orchestrator/nodes/reviewer.py` | Reviewer node — merges each agent branch, produces `ReviewReport`, cleans up worktrees |
| `backend/orchestrator/graph.py` | Extended 4-node graph: plan→spawn→run_workers→review. Auto-commit after each agent. |
| `backend/orchestrator/state.py` | Extended `GraphState` with team_composition, spawn_plan, worker_results, review_report |
| `tests/unit/test_phase2.py` | 10 new tests: planner parsing, spawner concurrency cap, reviewer merge/conflict/failure |

---

## Test Results

```
48 passed in 0.76s
```

All 48 tests green (38 from Phase 0+1, 10 new Phase 2 tests).

---

## End-to-End Verification

```
Task: "Create a file called hello.txt containing the text: Hello from HIVE agent"

Status: completed
Agent: builder-e2e-69-0
Cost: $0.051

Git log after run:
  3625363 hive: agent builder-e2e-69-0 output   ← auto-commit
  01987a8 merge: agent builder-e2e-69-0          ← reviewer merge
  170ea75 chore: init repo for HIVE
```

Full pipeline verified: Planner → Spawn → Worker runs → auto-commit → Reviewer merges → result returned.

---

## Key Decisions Made

1. **Planner uses inline prompt** — instructions + task both go in the `-p` argument to `claude`, not in `system_prompt`. This was necessary because the `system_prompt` field wasn't reliably forwarded.

2. **Brace-depth JSON extraction** — replaced regex `\{[\s\S]*\}` with a character-by-character depth tracker. Regex was matching too broadly when LLM added extra text after the JSON.

3. **Auto-commit in `_execute_worker`** — after the worker completes (success or failure), HIVE stages all changes in the worktree and commits. Without this, the Reviewer found 0 commits and had nothing to merge.

4. **DB path threading** — `create_session` in `run_session` uses the passed `db_path`; spawner's `create_agent` always uses the default `~/.hive/hive.db`. This means tests that pass a custom `db_path` must be aware that agent records go to the default DB. Unit tests for `_execute_worker` still work because they monkeypatch `create_agent` and `update_agent_status`.

5. **`claude:sonnet` everywhere in dev** — Opus reserved for production Planner/Reviewer only.

---

## Next Steps — Phase 3: Approval Modes

- `interrupt()` nodes at the plan→spawn boundary
- Confidence escalation: if Planner confidence < 0.7 → pause and ask user
- Approval mode per session: `full-auto` | `checkpoint` | `manual`
- `hive resume <session-id>` to continue after an interrupt
- CLI prompts for approving/rejecting the team composition before agents launch

---

*Previous phase summary preserved below for reference.*

---

# HIVE — Phase 1 Complete: Single Agent + LangGraph + SQLite

**Date:** 2026-05-13
**Phase:** 1 — Single Agent + State
**Status:** ✅ All done

## What Was Built

| File | Lines | Description |
|------|-------|-------------|
| `backend/persistence/db.py` | 99 | SQLite init, schema (sessions/agents/events/cost_log), WAL mode, async connection manager |
| `backend/persistence/events.py` | 151 | Event sourcing helpers: write_event, create_session, create_agent, list_sessions, get_session, write_cost |
| `backend/persistence/recovery.py` | 83 | Startup crash detection: checks active-agent PIDs, marks dead ones as 'crashed' |
| `backend/orchestrator/state.py` | 39 | `GraphState` TypedDict + `AgentResult` TypedDict for LangGraph |
| `backend/orchestrator/graph.py` | 211 | LangGraph graph: single `run_worker` node, `AsyncSqliteSaver` checkpointing, `run_session()` + `resume_session()` |
| `cli/hive.py` | 232 | Extended CLI: `hive run` (now via LangGraph + DB), `hive sessions`, `hive resume <id>` |
| `tests/unit/test_persistence.py` | 151 | 11 persistence tests (all DB operations) |
| `tests/unit/test_graph.py` | 142 | 2 graph node tests (success + error path) |

## Test Results

```
38 passed in 1.04s
```

## End-to-End Verification

```
$ hive run "Write one sentence about Python." --backend claude:sonnet
Session: f92c32e7  |  Backend: claude:sonnet
Python is a popular programming language...
Tokens: 3 in / 47 out  |  Cost: $0.0452
Session saved. Resume with: hive resume f92c32e7

$ hive sessions
ID         Status      Name                                          Last active
f92c32e7   completed   Write one sentence about Python.              2026-05-13 14:08:45
```
