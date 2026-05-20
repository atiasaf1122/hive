# Contributing to HIVE

Thanks for your interest. HIVE is small enough that most changes can be
discussed in a single PR conversation; please read this file before you
start so we agree on the rails.

## Dev environment

```bash
git clone https://github.com/<you>/hive.git
cd hive

# Backend — Python 3.11+ via uv (https://github.com/astral-sh/uv)
uv venv
uv pip install -e ".[dev]"

# Frontend — Tauri shell + React
cd desktop && npm install && cd ..
```

Run the backend alone:

```bash
uv run python -m uvicorn backend.main:app --port 8765
```

Run the desktop UI in dev mode:

```bash
cd desktop && npm run tauri:dev
```

Web-only preview (no Tauri shell, just Vite):

```bash
cd desktop && npm run dev
```

## Tests

Everything is `pytest` + `pytest-asyncio` for backend, `vitest` /
`tsc` / `vite build` for frontend. CI runs the full backend suite,
frontend type-check, and a production build on every PR.

```bash
pytest tests/ -q                      # 599 tests, ~70s
cd desktop && npx tsc -b               # type-check
cd desktop && npx vite build           # production bundle
```

A new feature is "done" when its tests are written. We don't merge code
that ships its tests "in a follow-up".

## Code style

- **Python**: full type hints everywhere; no bare `except`; no swallowing
  errors; `from __future__ import annotations` at the top of new modules.
- **TypeScript**: strict mode is on; no `any` unless interfacing with
  an untyped external library and you've narrowed it at the boundary.
- **Comments**: only when the *why* isn't obvious from the code. We
  delete comments that just restate the next line.
- **No new dependencies** without justifying them in the PR
  description — HIVE deliberately keeps the supply chain small.

## Architectural invariants

The seven invariants live in [CLAUDE.md](./CLAUDE.md) and are non-negotiable:

1. Orchestrator never calls `claude` CLI directly — always through `Worker`.
2. All state changes are SQLite events (append-only). Everything else is a projection.
3. Each agent that touches files runs in its own `git worktree`.
4. NDJSON pipeline: buffer chunks + split on `\n` + parse each line as JSON.
5. Approval correlation IDs survive backend restarts.
6. Rate-limit signals are first-class events.
7. Cost discipline: Opus only for Orchestrator + Reviewer.

If a PR breaks one of these it needs a separate discussion before code review.

## Commit messages

Conventional-commits style preferred:

```
feat(<scope>): one-line summary

Optional body explaining the why. Reference the relevant SUMMARY.md
section if it documents the broader pass.
```

`<scope>` examples: `safety`, `skills`, `summarizer`, `desktop`,
`packaging`, `v1`.

We don't squash-merge by policy — feature branches stay readable as a
chain of meaningful commits.

## Filing issues

- **Bug reports** should include: HIVE version (`hive --version`),
  OS, Python version, the reproducing steps, and the relevant lines
  from `~/.hive/hive.log`.
- **Security issues** — *do not* open a public issue. Email the
  contact listed in [SECURITY.md](./SECURITY.md).
- **Feature requests** — describe the user problem first, the
  proposed solution second. We close "wouldn't it be cool if…" issues
  without one.

## Adding skills / plugins / pipelines

These extend HIVE at runtime without a code change. See:

- Skills — `~/.hive/skills/<slug>/SKILL.md` (frontmatter + body).
- Plugins — MCP servers configured through the desktop UI.
- Pipelines — `hive pipelines create` or the Automations page.

If you have one you think the whole community should ship, open a PR
adding it to `backend/registries/curated.py`.
