# HIVE — Project CLAUDE.md

Source-of-truth for Claude Code working on HIVE — a personal multi-agent
Claude Code orchestrator (single user, local, runs on the owner's Claude Max
subscription). Read `docs/ARCHITECTURE_REVIEW_2026-07.md` for the current
assessment and roadmap; `ARCHITECTURE.md` for system internals;
`HIVE_BUILD_PLAN.md` is historical (Phases 0-9), not current state.

---

## Setup Decisions

| Question | Answer |
|----------|--------|
| OS | Windows 11 + WSL (Ubuntu) — backend runs inside WSL |
| Python package manager | `uv` (at `~/.local/bin/uv`) |
| API/WebSocket port | `8765` |
| Backend startup | Manual — `hive start`; desktop app: `cd desktop && npm run tauri:dev` |
| Distribution | None — personal tool, runs from source. Packaging/MSI/auto-updater are PARKED (runbook kept at `packaging/BUILD.md`) |
| Telegram bot | PARKED — code kept in `backend/telegram/`, not started with the backend, nothing in the live path imports it |
| Ollama | Supported when detected at `localhost:11434` (WSL host-IP fallback in `backend/detection.py`) |

---

## Architectural Invariants (never violate)

1. **Worker abstraction** — orchestrator never calls `claude` CLI directly. Always through the `Worker` Protocol (`backend/workers/base.py`).
2. **Append-only event log** — all agent activity lands in the SQLite `events` table. (Honest name: audit log — projections are updated directly, nothing replays events.)
3. **Git worktree per agent** — every agent that touches files runs in its own `git worktree`. This is also the security containment: workers run with `--dangerously-skip-permissions` (claude_cli.py), so the worktree boundary — not a permission broker — is what limits blast radius. A real sandbox (bwrap/container) is a future roadmap item.
4. **NDJSON pipeline** — always buffer chunks + split on `\n` + parse each line as JSON (`backend/workers/stream_parser.py`). No shortcuts.
5. **Approval correlation IDs** — every approval request carries a correlation ID that survives backend restarts (`pending_approvals` table).
6. **Rate-limit signals are first-class** — `system/api_retry` → RATE_LIMIT events pause the worker and surface to the UI.
7. **Cost discipline** — Sonnet is the workhorse; Haiku for mechanical/summarizer work; Opus only where the task warrants it. Model strings are `<backend>:<tier>` (e.g. `claude:sonnet`).

---

## Models — single source of truth

`backend/models.py`. Tier aliases (`opus`/`sonnet`/`haiku`) pass through to
`claude --model`, which resolves them to the latest model itself — never pin
dated model IDs in code (a test greps for retired IDs and fails the build).
Defaults: `DEFAULT_MODEL = "claude:sonnet"`, `HAIKU_MODEL = "claude:haiku"`.

---

## Session lifecycle (Phase A semantics)

`sessions.status`: `active` (runner attached) → `idle` (parked, no live
runner/agents — resumable from LangGraph checkpoint) → `closed` (user closed)
/ `failed` (runner exception). Nothing uses `completed`. On startup,
`run_startup_recovery` marks dead-PID agents crashed (`agents.pid` is written
at spawn) and reconciles orphaned `active` sessions to `idle`.
`POST /api/sessions/{id}/resume` re-attaches a runner; `/message` to an idle
session auto-resumes. `DELETE /api/sessions/{id}` is a real hard delete
(rows + checkpoints + worktrees).

---

## Tech Stack (locked)

- **Backend**: Python 3.11+, FastAPI + uvicorn, LangGraph 1.0+ (SqliteSaver checkpoints, `interrupt()` for approvals/multi-turn parking)
- **State store**: SQLite single file at `~/.hive/hive.db`
- **Scheduler**: APScheduler (in-process) — powers `backend/pipelines/`
- **Desktop**: Tauri 2 shell + React 18 + Vite + TypeScript + TailwindCSS in `desktop/` (port 1420). The old `frontend/` web UI was deleted in Phase A.
- **Embeddings**: sentence-transformers/all-MiniLM-L6-v2 (local) for skills search
- **No** OpenAI/external AI services, Redis, Postgres, or Celery

---

## Current state (July 2026)

- Version: **0.9.0** — one source of truth: `pyproject.toml`; `backend/main.py` reads installed metadata; desktop package.json/tauri.conf match.
- Tests: **428 passing** (`pytest -q`). Phase A deleted ~255 tests that covered removed dead code (command policy/executor suites, quality monitor).
- Phases 0-9C complete (see HIVE_BUILD_PLAN.md history). Phase 9D (MSI packaging) parked.
- **Phase A (fix the foundation) — DONE**: session lifecycle (PIDs/idle/resume), model registry, silent-stall fix, dead-code deletion, real session delete, version unification.
- **Phase B (next): make the swarm real** — per-agent subtask briefs + model tiers from the planner, `--session-id`/`--resume` in ClaudeCLIWorker, wire the summarizer into review, wire validators into trust, hybrid skills search in the run loop, LLM review on merge conflict only.
- **Phase C: MCP execution** — per-agent `--mcp-config` + `--strict-mcp-config`, capability registry (playwright/github/context7), ~50-tool budget.
- **Phase D: META layer** — GOAL.md/ROADMAP.md per project, pattern_detector as deterministic trigger, scheduled self-analysis, human-gated self-fixes.
- Full rationale + designs: `docs/ARCHITECTURE_REVIEW_2026-07.md`.

## Known gaps (deliberate, don't "fix" casually)

- `run_workers_node` still sends every agent the same prompt — per-agent briefs are Phase B, not a bug to patch ad hoc.
- The reviewer (`backend/orchestrator/nodes/reviewer.py`) is git-merge only; no LLM review yet (Phase B).
- The summarizer (`backend/summarizer/`), validators (`backend/validation/validators.py`), hybrid skills search, and `safety/pattern_detector.py` are built but not wired into the run loop — Phases B/D wire them; don't delete.
- Tests run against the real `~/.hive/hive.db` via the FastAPI lifespan (TestClient) — they pollute the live sessions table. Worth isolating (HIVE_DIR env) when convenient.

---

## Coding Standards

- Python 3.11+ with full type hints
- No `try/except` that silently swallows errors (existing best-effort writes log at warning)
- No `--bare` flag on claude CLI (blocks OAuth); always `--output-format stream-json`
- Every agent that runs gets its own git worktree, even read-only agents
- Tests before a phase is marked done (pytest + pytest-asyncio); typecheck desktop with `cd desktop && npx tsc --noEmit`
