# HIVE

A local AI agent swarm running on top of your Claude Max subscription.

You describe a task. An **Orchestrator** decides whether to chat, spawn
specialist agents, or both. Each agent runs in its own git worktree.
A **Reviewer** merges the work back to main. Sessions stay open for as
long as you want them — you talk to the orchestrator like a colleague.
Cron-scheduled and webhook-triggered pipelines turn one-off tasks into
recurring automation. A Tauri 2 desktop shell wraps the whole thing in a
native window. (A Telegram bot for phone approvals exists but is parked —
see CLAUDE.md.)

```
┌────────────────────────────────────────────────────────────────┐
│  You ↔ Orchestrator (always live)                              │
│         ├─ chat reply                                          │
│         └─ spawn team ─→ approval ─→ workers ─→ reviewer       │
│                                       │                        │
│                                  (git worktrees)               │
└────────────────────────────────────────────────────────────────┘
```

| | |
|---|---|
| Backend tests | **428** (`pytest tests/`) |
| Frontend | Tauri 2 + React 18 + Vite + TypeScript + TailwindCSS |
| Backend | Python 3.11+, FastAPI + uvicorn, LangGraph 1.x, SQLite (WAL) |
| Status | v0.9 personal tool — roadmap in [docs/ARCHITECTURE_REVIEW_2026-07.md](./docs/ARCHITECTURE_REVIEW_2026-07.md) |

---

## Requirements

| | Minimum | Recommended |
|---|---------|-------------|
| OS | Linux / macOS / WSL2 / Windows 11 | Ubuntu in WSL2, or native Windows for the desktop shell |
| Python | 3.11 | 3.11 or 3.12 |
| Node | 20.x | 20.x |
| Rust | only if you build the Tauri desktop shell | 1.75+ |
| `claude` CLI | 1.0+ | latest |
| Git | 2.30+ (worktree support) | latest |
| Ollama (optional) | 0.1+ if you want local models | — |

---

## Install

```bash
git clone https://github.com/<you>/hive.git ~/hive
cd ~/hive

# Backend
uv pip install -e ".[dev]"

# Desktop (Tauri shell + React UI)
cd desktop && npm install && cd ..

# First-time setup — detects backends, asks 5 questions, writes ~/.hive/
hive onboard
```

`hive onboard` checks that `claude`, `git`, and (optionally) Ollama are
reachable, runs `claude setup-token` if you don't already have an OAuth
token, creates `~/.hive/`, and initialises the SQLite database.

---

## Run

```bash
# Terminal 1 — backend (FastAPI + WebSocket on :8765)
hive start

# Terminal 2 — desktop dev (Tauri opens its own window automatically)
cd desktop && npm run tauri:dev
```

If you only want the web preview:

```bash
cd desktop && npm run dev    # plain Vite on http://localhost:1420
```

Packaged builds (`.msi` etc.) are PARKED — HIVE is a run-from-source
personal tool. The someday-runbook lives at [packaging/BUILD.md](./packaging/BUILD.md).

---

## Daily Use

### Desktop UI

Open a project tab, type a task, hit `Ctrl+Enter`.

- A question → orchestrator answers inline.
- A real task → orchestrator proposes a team. In `full-auto` at high
  confidence it runs immediately; otherwise it pauses for you to
  approve / reject / edit.
- Agents run in parallel git worktrees. Reviewer merges back to main.
- The session **stays open** — keep messaging the orchestrator.
- `Ctrl+Shift+N` opens a fresh window; `Ctrl+K` is global search.

### CLI

```bash
hive run "Add type hints to backend/api/*.py" --approval-mode checkpoint
hive sessions                 # list active sessions
hive resume <session-id>      # pick up an interrupted session
hive status                   # which workers + models are available
```

### Pipelines (recurring tasks)

```bash
hive pipelines create "Daily haiku" --task "Write a Python haiku" --schedule "0 17 * * *"
hive pipelines list           # all pipelines + webhook URLs
hive pipelines run <id>       # fire immediately
hive pipelines runs <id>      # past run history
```

### Telegram (parked)

The bot code is kept in `backend/telegram/` but no longer starts with the
backend. Restore the `start_bot()` lifespan call in `backend/main.py`
(git history: Phase 7) to re-enable it.

### Skills

```bash
hive skills create python-testing --description "Write pytest tests"
hive skills import ~/.hive/skills/python-testing/SKILL.md
hive skills test "write unit tests for my API"   # which skills match
```

Skills are surface-area boosters — when a worker spawns, the top-3 most
semantically relevant skills are injected into its system prompt. The
v1.0 hybrid search blends embedding similarity, BM25, and tag overlap;
the optional Haiku rerank gate is wired through `?session_id=<id>`.

---

## Safety & Security

Honest posture for a single-user local tool: workers run the `claude`
CLI with `--dangerously-skip-permissions`, so the **git worktree is the
containment boundary** — a real sandbox (bwrap/container) is a future
roadmap item. What IS enforced today:

1. **Hard stops** (`backend/safety/hard_stops.py`) — ceilings on a
   single autonomous run: token budget, duration, concurrent agents,
   same-file edits. Per-session overrides
   (`backend/safety/overrides.py`) tighten or loosen the defaults.
2. **Circuit breakers** (`backend/safety/circuit_breaker.py`) — a
   worker that keeps failing gets skipped until its breaker half-opens.
3. **Trust scores** (`backend/validation/trust.py`) — per-model
   completion bookkeeping. (The deterministic validators in
   `backend/validation/validators.py` exist but aren't wired into the
   run loop yet — Phase B.)

See [SECURITY.md](./SECURITY.md) for the vulnerability-reporting policy.

---

## Configuration

| File | Purpose |
|------|---------|
| `~/.hive/hive.db` | SQLite — sessions, agents, events, costs, pipelines, skills |
| `~/.hive/credentials.json` | `claude` OAuth token (chmod 0600) |
| `~/.hive/telegram.json` | Telegram token + allowed chat IDs (chmod 0600) |
| `~/.hive/skills/<name>/SKILL.md` | Skill files — YAML frontmatter + Markdown body |
| `~/.hive/worktrees/<session>/<agent>/` | Per-agent git worktrees |
| `~/.hive/sessions/<id>/` | Per-session workspace |

Override the data dir with `HIVE_DIR=/path/to/store`.

### Useful env vars

| Variable | Default | Effect |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | — | OAuth token for `claude` CLI |
| `ANTHROPIC_API_KEY` | — | Enables ClaudeAPIWorker fallback |
| `CLAUDE_STREAM_IDLE_TIMEOUT_MS` | `600000` | Idle ceiling for the NDJSON parser |
| `HIVE_HAIKU_BUDGET_TOKENS` | `50000` | Per-session Haiku budget |
| `HIVE_HAIKU_RERANK_BUDGET_TOKENS` | `10000` | Skills rerank sub-budget |
| `HIVE_HAIKU_CROSSCHECK_BUDGET_TOKENS` | `20000` | Cross-check sub-budget |
| `HIVE_HAIKU_SUMMARIZER_BUDGET_TOKENS` | `30000` | Summarizer sub-budget |

---

## Documentation

- **[ARCHITECTURE.md](./ARCHITECTURE.md)** — full design walkthrough + all ADRs.
- **[docs/ARCHITECTURE_REVIEW_2026-07.md](./docs/ARCHITECTURE_REVIEW_2026-07.md)** — current assessment + A→B→C→D roadmap.
- **[CLAUDE.md](./CLAUDE.md)** — instructions for Claude Code working on HIVE itself.
- **[HIVE_BUILD_PLAN.md](./HIVE_BUILD_PLAN.md)** — original spec (Hebrew, historical).

---

## Cost Discipline

HIVE is built to live inside a single Claude Max subscription:

- Opus is reserved for the **Orchestrator** and **Reviewer**.
- Workers default to Sonnet (≈10× cheaper per call).
- Haiku is used for cross-check / skills rerank / Summarizer.
- The optional `OllamaWorker` handles translation, summarisation, and
  doc-reading at zero token cost.
- Per-session cost log lives in `cost_log`. `GET /api/cost/summary`
  surfaces per-session and per-day spend so you can spot runaway burns.

---

## Tests

```bash
pytest tests/                        # 599 backend tests, ~70s
cd desktop && npx tsc -b && npx vite build   # frontend type-check + build
```

GitHub Actions runs both on every PR — see `.github/workflows/ci.yml`.

---

## License

Personal use only. HIVE acts on behalf of one Claude Max subscriber.
Do not deploy as a multi-tenant service on a shared Anthropic subscription.
