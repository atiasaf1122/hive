"""SQLite connection management and schema for HIVE.

Single file DB at ~/.hive/hive.db. All tables created on first connect.
Migrations are append-only: new tables/columns added, nothing dropped.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

HIVE_DIR = Path(os.environ.get("HIVE_DIR", Path.home() / ".hive"))
DB_PATH = HIVE_DIR / "hive.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT 'one-shot',   -- 'one-shot' | 'persistent'
    status      TEXT NOT NULL DEFAULT 'active',     -- 'active' | 'completed' | 'failed' | 'archived'
    approval_mode TEXT NOT NULL DEFAULT 'full-auto',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    last_active TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    role            TEXT NOT NULL DEFAULT 'worker',
    model           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'completed' | 'failed' | 'crashed'
    worktree_path   TEXT NOT NULL DEFAULT '',
    pid             INTEGER,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at        TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    agent_id    TEXT NOT NULL,
    type        TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS cost_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    agent_id    TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS skills (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL,
    tags         TEXT NOT NULL DEFAULT '[]',
    path         TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    embedding    BLOB,
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);


CREATE TABLE IF NOT EXISTS pipelines (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    task         TEXT NOT NULL,
    model        TEXT NOT NULL DEFAULT 'claude:sonnet',
    approval_mode TEXT NOT NULL DEFAULT 'full-auto',
    schedule     TEXT,
    webhook_token TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_run_at  TEXT,
    next_run_at  TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id           TEXT PRIMARY KEY,
    pipeline_id  TEXT NOT NULL REFERENCES pipelines(id),
    session_id   TEXT,
    triggered_by TEXT NOT NULL DEFAULT 'manual',
    status       TEXT NOT NULL DEFAULT 'running',
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_pl ON pipeline_runs(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_agent   ON events(agent_id);
CREATE INDEX IF NOT EXISTS idx_agents_session ON agents(session_id);

-- Phase 10 (Production v1.0) — command sandbox + circuit breakers.
CREATE TABLE IF NOT EXISTS command_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              DATETIME NOT NULL DEFAULT (datetime('now')),
    project_id      TEXT NOT NULL DEFAULT '',
    agent_id        TEXT NOT NULL DEFAULT '',
    command         TEXT NOT NULL,
    working_dir     TEXT NOT NULL DEFAULT '',
    classification  TEXT NOT NULL,         -- 'allowed' | 'confirmed' | 'blocked'
    decision_source TEXT NOT NULL DEFAULT 'system',  -- 'system' | 'custom'
    matched_pattern TEXT,
    exit_code       INTEGER,
    stdout_excerpt  TEXT,                  -- first 500 chars, truncated
    stderr_excerpt  TEXT,                  -- first 500 chars, truncated
    duration_ms     INTEGER,
    user_approved   INTEGER                -- 1 = yes, 0 = no, NULL = n/a
);
CREATE INDEX IF NOT EXISTS idx_command_audit_ts      ON command_audit(ts);
CREATE INDEX IF NOT EXISTS idx_command_audit_project ON command_audit(project_id);
CREATE INDEX IF NOT EXISTS idx_command_audit_agent   ON command_audit(agent_id);

-- Phase 10 (Production v1.0) — per-project safety overrides.
-- One row per session; absent = inherit the build-time HARD_STOPS defaults.
CREATE TABLE IF NOT EXISTS session_safety_overrides (
    session_id                       TEXT PRIMARY KEY,
    max_tokens_per_autonomous_run    INTEGER,
    max_session_duration_hours       REAL,
    max_concurrent_agents            INTEGER,
    max_same_file_edits              INTEGER,
    notify_at_burn_ratio             REAL,
    updated_at                       DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- Phase 10 (Production v1.0) — trust scores (Section 5.5).
CREATE TABLE IF NOT EXISTS worker_trust_scores (
    worker_id              TEXT PRIMARY KEY,
    successful_completions INTEGER NOT NULL DEFAULT 0,
    failed_validations     INTEGER NOT NULL DEFAULT 0,
    total_sessions         INTEGER NOT NULL DEFAULT 0,
    last_updated           DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- Persistent approval queue (invariant #5: correlation IDs survive restart).
-- Without this, every team_approval / awaiting_input interrupt that the user
-- hasn't yet answered is silently dropped on backend restart, and the
-- orchestrator's awaiting Future never resolves.
CREATE TABLE IF NOT EXISTS pending_approvals (
    correlation_id   TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES sessions(id),
    agent_id         TEXT NOT NULL DEFAULT '',
    request_payload  TEXT NOT NULL DEFAULT '{}',
    status           TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'approved' | 'rejected' | 'expired'
    created_at       DATETIME NOT NULL DEFAULT (datetime('now')),
    resolved_at      DATETIME,
    response_payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_session ON pending_approvals(session_id);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_status  ON pending_approvals(status);
"""


def ensure_hive_dir() -> Path:
    HIVE_DIR.mkdir(parents=True, exist_ok=True)
    return HIVE_DIR


async def init_db(path: Path = DB_PATH) -> None:
    """Create DB file and apply schema. Safe to call multiple times."""
    ensure_hive_dir()
    async with aiosqlite.connect(path) as conn:
        await conn.executescript(_SCHEMA)
        await conn.commit()


@asynccontextmanager
async def get_conn(path: Path = DB_PATH):
    """Async context manager yielding an aiosqlite connection."""
    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        yield conn


def init_db_sync(path: Path = DB_PATH) -> None:
    """Synchronous init used by CLI startup before async loop."""
    ensure_hive_dir()
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
