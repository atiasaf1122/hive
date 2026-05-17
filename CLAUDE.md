# HIVE — Project CLAUDE.md

This is the source-of-truth for Claude Code building HIVE.
Read HIVE_BUILD_PLAN.md for full architecture. This file tracks setup decisions and phase status.

---

## Setup Decisions (answered before Phase 0)

| Question | Answer |
|----------|--------|
| OS | Windows 11 + WSL (Ubuntu) — backend runs inside WSL |
| Python package manager | `uv` |
| API/WebSocket port | `8765` |
| Backend startup | Manual — `hive start` (no systemd service for now) |
| Telegram bot | Phase 7 — not needed yet |
| Ollama | Not on current laptop; target PC will have it. **The system must fully support OllamaWorker** from Phase 0 — design for it even without it being active. |

---

## Architectural Invariants (from HIVE_BUILD_PLAN.md — never violate)

1. **Worker abstraction** — orchestrator never calls `claude` CLI directly. Always through `Worker` interface.
2. **Event sourcing** — all state changes are events in SQLite (append-only). Everything else is a projection.
3. **Git Worktree per agent** — each agent that touches files runs in its own `git worktree`. No sharing.
4. **NDJSON pipeline** — always buffer chunks + split on `\n` + parse each line as JSON. No shortcuts.
5. **Approval correlation IDs** — every approval request carries a correlation ID that survives backend restarts.
6. **Rate-limit signals are first-class** — `system/api_retry` events update UI, pause non-critical workers, alert user.
7. **Cost discipline** — Opus only for Orchestrator + Reviewer. Sonnet for 90% of workers. Haiku/Ollama for simple tasks.

---

## Tech Stack (locked)

- **Backend**: Python 3.11+, FastAPI + uvicorn, LangGraph 1.0+
- **State store**: SQLite (single file) + LangGraph SqliteSaver
- **Scheduler**: APScheduler 3.x (AsyncIOScheduler, in-process)
- **Telegram**: aiogram v3 (Phase 7)
- **Frontend**: React 18 + Vite + TypeScript + TailwindCSS
- **Tree viz**: @xyflow/react (react-flow) v12 + dagre layout
- **Embeddings**: sentence-transformers/all-MiniLM-L6-v2 (local, Phase 4)
- **Ollama**: HTTP client to `http://localhost:11434` — always implemented, activated when available

---

## WSL Development Notes

- Backend code lives in WSL filesystem (e.g., `~/hive/` or `/home/<user>/hive/`)
- Frontend dev server runs in WSL but accessible from Windows browser at `localhost:5173`
- `hive start` script launches both backend (port 8765) and frontend dev server
- All paths in backend code use POSIX style (`/home/...`), not Windows paths
- Git worktrees created under `~/.hive/worktrees/`
- Credentials stored at `~/.hive/credentials.json` with chmod 600

---

## Workers

Three implementations, always all present in code:

| Worker | Backend | When active |
|--------|---------|-------------|
| `ClaudeCLIWorker` | `claude` CLI subprocess via OAuth | Always (default) |
| `ClaudeAPIWorker` | Anthropic API via `ANTHROPIC_API_KEY` | Fallback if ToS changes |
| `OllamaWorker` | HTTP to `localhost:11434` | When Ollama detected at startup |

Backend detection at startup:
1. Check `claude --version` → ClaudeCLIWorker available
2. Check `ANTHROPIC_API_KEY` env → ClaudeAPIWorker available
3. `GET http://localhost:11434/api/tags` → OllamaWorker available + list models

---

## Phase Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 0 | ✅ Complete | Worker bedrock — 3 Workers + NDJSON pipeline + backend detection |
| Phase 1 | ✅ Complete | Single agent + LangGraph + SQLite persistence |
| Phase 2 | ✅ Complete | Multi-agent + worktrees + parallel execution |
| Phase 3 | ✅ Complete | Approval modes + confidence escalation |
| Phase 4 | ✅ Complete | Skills registry + embedding search |
| Phase 5 | ✅ Complete | Web UI + WebSocket |
| Phase 6 | ✅ Complete | Persistent pipelines + scheduler |
| Phase 7 | 🔲 Not started | Telegram bot |
| Phase 8 | 🔲 Not started | Polish + docs |

---

## Coding Standards

- Python 3.11+ with full type hints everywhere
- No OpenAI API or external AI services — only `claude` CLI subprocess and MCP servers
- No Redis, Postgres, Celery — SQLite + APScheduler in-process is enough
- No `try/except` that silently swallows errors
- No `--bare` flag on claude CLI (blocks OAuth)
- Always use `--output-format stream-json` — never parse raw text output
- Every agent that runs gets its own git worktree, even read-only agents
- Tests written before phase is marked done (pytest + pytest-asyncio)

---

## Definition of Done — Phase 0

- [ ] `Worker` abstract interface defined (`base.py`)
- [ ] `ClaudeCLIWorker` — subprocess with process groups, correct env vars, stream_parser
- [ ] `OllamaWorker` — HTTP streaming client, events normalized to unified format
- [ ] `stream_parser.py` — buffer + `\n` split + per-line JSON parse, all event types handled
- [ ] Backend detection at startup (logs which backends are available)
- [ ] `hive run "task" --backend claude:sonnet` works end-to-end
- [ ] `hive run "task" --backend ollama:model` works (or gracefully reports Ollama unavailable)
- [ ] Unit tests: mock subprocess, all event types, fallback logic
- [ ] Both backends emit events in identical unified format
