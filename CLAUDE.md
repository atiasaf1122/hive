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
3. **Git worktree per agent + PreToolUse guard hook** — every agent that touches files runs in its own `git worktree`. This is the security containment: workers run with `--dangerously-skip-permissions` (claude_cli.py), so the worktree boundary — not a permission broker — is what limits blast radius. Layered on top (F1): each Claude worker's worktree gets a `.claude/settings.json` registering a deterministic PreToolUse guard (`backend/guard/pretooluse_guard.py`) that denies a short catastrophic list (rm -rf outside the worktree, credential-path reads, fork bombs, device destruction). Verified live: **a PreToolUse deny blocks the tool even under `--dangerously-skip-permissions`** — hooks fire before the permission-mode check and can only tighten, never weaken. This replaces the deleted Phase-A executor (which the CLI never routed through) and supersedes the bwrap/container roadmap item at ~5% of the effort; a real OS sandbox is no longer the plan. Local (Ollama) workers have no Bash tool (file-block harness only), so the guard is Claude-worker-scoped by construction.
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

## Current state — v1.0.0 (July 2026)

Build phases A→G complete. HIVE has graduated to real use. Full rationale +
per-phase progress log: `docs/ARCHITECTURE_REVIEW_2026-07.md`.

- Version: **1.0.0** — one source of truth: `pyproject.toml`; `backend/main.py` reads installed metadata; desktop package.json/tauri.conf match.
- Tests: **620 passing** (`pytest -q`), isolated via `HIVE_DIR` temp dir (conftest); hermetic — no real model calls or `~/.hive/hive.db` pollution.
- Golden suite: 10 specs through the REAL pipeline (`hive golden run`) — no known-flaky specs; every spec passes deterministically or its failure means something.

### What each phase contributed
- **A — foundation**: session lifecycle (PIDs/idle/resume from LangGraph checkpoints), model registry, real session delete, silent-stall fix, dead-code deletion.
- **B — the swarm is real**: per-agent subtask briefs + model tiers from the planner; `--session-id`/`--resume` context reuse; summarizer + validators wired into trust; hybrid skills search; Opus `llm_review` on merge-conflict/validation-failure only.
- **C — MCP execution**: per-agent `--mcp-config --strict-mcp-config`, curated server catalog (playwright/github/context7/filesystem), preflight doctor, per-agent equipment chips.
- **D — META / learning**: the lessons store (grounded writes, groundedness gate, conservative retrieval, hygiene); D2 plan gate; D3 compaction; D4 file-overlap waves; D5 golden suite; D6 estimates; D7 trajectory replay; D8 on-demand META agent.
- **E — hybrid economics**: local Ollama pool (discovery, capability catalog, VRAM manager), planner routes across it, task-shape router (SOLO/SWARM/CHAT), local meta-tasks; measured −65% cost on golden.
- **F — hardening**: planner+all costs in cost_log; PreToolUse guard hook; Stop/SubagentStop push signals; salvage review for failed agents' committed work; producer/consumer runtime net.
- **G — close-out**: needs_tools first-class classifier field; contract-first briefs; flask-todo de-flaked (must-agree deliverables → SWARM); multi-wave + local-multifile proven; v1.0.0.

## Routing rules (how a request becomes work)
1. **Task-shape classifier** (local 8B, else Haiku; `backend/orchestrator/task_router.py`) emits `shape` (SOLO/SWARM/CHAT), `mechanical`, and `needs_tools`.
   - CHAT → answered in-session, zero spawns. SOLO → one worker, thin pipe (no planner/gate). SWARM → full planner decomposition.
   - Any task naming two deliverables that must AGREE (API+tests, module+consumer) is SWARM, never solo.
2. **needs_tools=true never routes to a local worker** (local has no tool loop — file-block harness only); browser-shaped solos get the playwright MCP. A keyword scan is a validation backstop that logs `CLASSIFIER_DISAGREEMENT` and routes to the safer (Claude) verdict.
3. **Local (`ollama:<model>`) is preferred for mechanical, fully-specified work** (`needs_tools=false`); Claude tiers for reasoning, MCP, ambiguity. Local declares a `fallback` tier used when RAM/VRAM headroom vanishes at spawn (a `model/fallback` event fires).
4. **Cost discipline** (invariant #7): Sonnet workhorse, Haiku mechanical/meta, Opus only for `llm_review`/salvage/META. `<backend>:<tier>` strings.

## Operating HIVE
- **Run a session**: `hive start` (backend), desktop `cd desktop && npm run tauri:dev`; send a message in a project. The composer's Auto/Solo/Swarm/Chat selector overrides the classifier.
- **Golden regression**: `hive golden run` (all 10 specs, real cost ~$1.5–2) or `--only <spec>`. Manual, never CI.
- **META (on-demand only)**: `hive meta` (or Settings→Lessons "Analyze & Advise") — one Opus pass over HIVE's own stats; advises, never auto-executes (~$0.34/run). The amber **"Recurring failures — run META?"** badge appears when ≥3 same-class failures cluster in 24h (`GET /api/meta/nudge`) — a nudge, not a schedule.
- **Cost breakdown**: click the cost figure in a project's agents bar for a per-role breakdown (planner/gate/workers/summarizers/review, 🏠 $0 local rows, saved-via-local).

## Known gaps (deliberate — don't "fix" casually)
- **No OS sandbox** — containment is worktree isolation + the F1 PreToolUse guard (see invariant #3). bwrap/container is off the roadmap; revisit only if `GUARD_TRIPPED` clusters show the catastrophic list is insufficient.
- **Lessons store is small (n≈2)** — retrieval bar (0.35) and 3-strike archive are unexercised at volume; real use grows the material.
- **Telegram** (`backend/telegram/`) stays parked — untested against B–G changes.
- **Distillation stays on Haiku** by default (`HIVE_LOCAL_INTERNAL=on` forces local) — the E4 quality check found the local 8B confabulated a lesson; summarization is local-first.

---

## Coding Standards

- Python 3.11+ with full type hints
- No `try/except` that silently swallows errors (existing best-effort writes log at warning)
- No `--bare` flag on claude CLI (blocks OAuth); always `--output-format stream-json`
- Every agent that runs gets its own git worktree, even read-only agents
- Tests before a phase is marked done (pytest + pytest-asyncio); typecheck desktop with `cd desktop && npx tsc --noEmit`
