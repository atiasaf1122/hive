# HIVE

A local AI agent swarm running on top of your Claude Max subscription.

You describe a task. An **Orchestrator** decides whether to chat, spawn
specialist agents, or both. Each agent runs in its own git worktree.
A **Reviewer** merges the work back to main. Sessions stay open for as
long as you want them — you talk to the orchestrator like a colleague.
Cron-scheduled and webhook-triggered pipelines turn one-off tasks into
recurring automation. A Telegram bot lets you steer projects from your
phone.

```
┌────────────────────────────────────────────────────────────────┐
│  You ↔ Orchestrator (always live)                              │
│         ├─ chat reply                                          │
│         └─ spawn team ─→ approval ─→ workers ─→ reviewer       │
│                                       │                        │
│                                  (git worktrees)               │
└────────────────────────────────────────────────────────────────┘
```

---

## Requirements

| | Minimum | Recommended |
|---|---------|-------------|
| OS | Linux / macOS / WSL2 | Ubuntu in WSL2 |
| Python | 3.11 | 3.11 or 3.12 |
| Node | 20.x (for the web UI) | 20.x |
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

# Frontend
cd frontend && npm install && cd ..

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

# Terminal 2 — frontend dev server (Vite on :5173)
cd frontend && npm run dev
```

Open <http://localhost:5173>.

---

## Daily Use

### Web UI

Open a session, type a task, hit ⌘↵.

- Empty chat or a question → orchestrator answers inline.
- A real task → orchestrator proposes a team. In `full-auto` mode at high
  confidence it runs immediately; in `checkpoint` mode it pauses for you
  to approve / reject / edit before agents launch.
- Agents run in parallel git worktrees. Reviewer merges results back to
  your main branch.
- The session **stays open** — keep messaging the orchestrator. Hit
  **Close project** in the sidebar to end it.

### CLI

```bash
# One-shot
hive run "Add type hints to backend/api/*.py" --approval-mode checkpoint

# Long-running session control
hive sessions                 # list active sessions
hive resume <session-id>      # pick up an interrupted session

# Backends + status
hive status                   # which workers + models are available
```

### Pipelines (recurring tasks)

```bash
hive pipelines create "Daily haiku" --task "Write a Python haiku" --schedule "0 17 * * *"
hive pipelines list           # see all pipelines + webhook URLs
hive pipelines run <id>       # fire immediately
hive pipelines runs <id>      # past run history
```

### Telegram

```bash
hive telegram setup --token <bot-token>     # from @BotFather
# Open your bot in Telegram, send /start, copy the chat ID it prints, then:
hive telegram allow <chat-id>
hive start                                  # bot starts with backend
```

In Telegram: `/sessions`, `/attach <id>`, free-text chat to the
orchestrator, inline ✓ / ✗ buttons for approvals, `/close`.

### Skills

```bash
hive skills create python-testing --description "Write pytest tests"
hive skills import ~/.hive/skills/python-testing/SKILL.md
hive skills test "write unit tests for my API"   # show which skills match
```

Skills are surface-area boosters — when a worker spawns, the top-3 most
semantically similar skills are injected into its system prompt. No
manual wiring per task.

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

---

## Documentation

- **[ARCHITECTURE.md](./ARCHITECTURE.md)** — full design walkthrough, every library, every decision.
- **[SUMMARY.md](./SUMMARY.md)** — phase-by-phase build log.
- **[HIVE_BUILD_PLAN.md](./HIVE_BUILD_PLAN.md)** — original spec (Hebrew).
- **[CLAUDE.md](./CLAUDE.md)** — instructions for Claude Code working on HIVE itself.

---

## Cost Discipline

HIVE is built to live inside a single Claude Max subscription:

- Opus is reserved for the **Orchestrator** and **Reviewer**.
- Workers default to Sonnet (≈10× cheaper per call).
- The optional `OllamaWorker` handles translation, summarisation, and
  doc-reading at zero token cost.
- Cost dashboard (`GET /api/cost/summary`) shows per-session and per-day
  spend so you can spot runaway burns.

---

## Tests

```bash
uv run python -m pytest tests/unit/         # ~160 tests, ~25s
```

Phases 0–8 each ship with their own test file. No CI required — they
run locally before each phase is marked done.

---

## License

Personal use only. HIVE acts on behalf of one Claude Max subscriber.
Do not deploy as a multi-tenant service on a shared Anthropic subscription.
