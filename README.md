# HIVE

A local AI agent swarm running on top of your Claude Max subscription.

You describe a task. An **Orchestrator** decides whether to chat, spawn
specialist agents, or both. Each agent runs in its own git worktree.
A **Reviewer** merges the work back to main. Sessions stay open for as
long as you want them — you talk to the orchestrator like a colleague.
Cron-scheduled and webhook-triggered pipelines turn one-off tasks into
recurring automation. A Telegram bot lets you steer projects from your
phone. A Tauri 2 desktop shell wraps the whole thing in a native window.

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
| Backend tests | **599** (`pytest tests/`) |
| Frontend | Tauri 2 + React 18 + Vite + TypeScript + TailwindCSS |
| Backend | Python 3.11+, FastAPI + uvicorn, LangGraph 1.x, SQLite (WAL) |
| Status | v1.0-rc work in progress — see [SUMMARY.md](./SUMMARY.md) for the live build log |

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

For production builds see [packaging/BUILD.md](./packaging/BUILD.md) — the
Tauri `.msi` / `.dmg` / `.AppImage` flow.

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

### Telegram

```bash
hive telegram setup --token <bot-token>     # from @BotFather
hive telegram allow <chat-id>
hive start                                  # bot starts with backend
```

In Telegram: `/sessions`, `/attach <id>`, free-text chat to the
orchestrator, inline ✓ / ✗ buttons for approvals, `/close`.

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

HIVE ships three layers of guard-rails the orchestrator enforces:

1. **Command policy** (`backend/security/command_policy.py`) — every
   shell command an agent issues is classified `allowed` / `confirmed`
   / `blocked` against pattern lists. `ALWAYS_BLOCKED` patterns cannot
   be overridden even in BLIND_AUTO mode.
2. **Hard stops** (`backend/safety/hard_stops.py`) — non-overridable
   ceilings on a single autonomous run: token budget, duration,
   concurrent agents, same-file edits, VRAM, disk.
   Per-session overrides (`backend/safety/overrides.py`) let users
   tighten or loosen the defaults under explicit accept-responsibility.
3. **Validation stack** (`backend/validation/`) — every worker
   completion runs the deterministic validators (file existence, git
   diff, tests in audit log) before the agent's worktree is released.
   An optional Haiku semantic cross-check
   (`backend/llm/haiku.py` + `POST /api/validation/cross-check`) scores
   how well the worker's evidence backs its claim.

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
- **[SUMMARY.md](./SUMMARY.md)** — phase-by-phase build log (newest first).
- **[CONTRIBUTING.md](./CONTRIBUTING.md)** — how to set up a dev env and propose changes.
- **[SECURITY.md](./SECURITY.md)** — vulnerability-reporting policy.
- **[CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)** — community standards.
- **[CLAUDE.md](./CLAUDE.md)** — instructions for Claude Code working on HIVE itself.
- **[HIVE_BUILD_PLAN.md](./HIVE_BUILD_PLAN.md)** — original spec (Hebrew).

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
