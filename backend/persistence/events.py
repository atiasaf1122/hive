"""Event sourcing helpers — write and read HiveEvents from SQLite.

Every state change in HIVE is recorded here. All other state (sessions,
agents, cost) is derived from these events or written alongside them.
"""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from backend.persistence.db import DB_PATH, get_conn
from backend.workers.base import HiveEvent


async def write_event(event: HiveEvent, path: Path = DB_PATH) -> None:
    """Append a HiveEvent to the events table."""
    payload = event.model_dump(exclude={"agent_id", "session_id", "ts", "type"})
    # Drop None values to keep payloads compact
    payload = {k: v for k, v in payload.items() if v is not None}

    async with get_conn(path) as conn:
        await conn.execute(
            "INSERT INTO events (ts, session_id, agent_id, type, payload_json) VALUES (?,?,?,?,?)",
            (event.ts, event.session_id, event.agent_id, str(event.type), json.dumps(payload)),
        )
        await conn.commit()


async def write_events_batch(events: list[HiveEvent], path: Path = DB_PATH) -> None:
    """Write multiple events in a single transaction — more efficient for bursts."""
    rows = []
    for e in events:
        payload = e.model_dump(exclude={"agent_id", "session_id", "ts", "type"})
        payload = {k: v for k, v in payload.items() if v is not None}
        rows.append((e.ts, e.session_id, e.agent_id, str(e.type), json.dumps(payload)))

    async with get_conn(path) as conn:
        await conn.executemany(
            "INSERT INTO events (ts, session_id, agent_id, type, payload_json) VALUES (?,?,?,?,?)",
            rows,
        )
        await conn.commit()


async def get_session_events(session_id: str, path: Path = DB_PATH) -> list[dict]:
    """Return all events for a session in chronological order."""
    async with get_conn(path) as conn:
        cursor = await conn.execute(
            "SELECT ts, agent_id, type, payload_json FROM events WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
    return [
        {"ts": r["ts"], "agent_id": r["agent_id"], "type": r["type"], "payload": json.loads(r["payload_json"])}
        for r in rows
    ]


async def create_session(
    session_id: str,
    name: str = "",
    path: str = "",
    session_type: str = "one-shot",
    approval_mode: str = "full-auto",
    db_path: Path = DB_PATH,
) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO sessions (id, name, path, type, approval_mode) VALUES (?,?,?,?,?)",
            (session_id, name, path, session_type, approval_mode),
        )
        await conn.commit()


async def update_session_status(session_id: str, status: str, db_path: Path = DB_PATH) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute(
            "UPDATE sessions SET status=?, last_active=datetime('now') WHERE id=?",
            (status, session_id),
        )
        await conn.commit()


async def create_agent(
    agent_id: str,
    session_id: str,
    role: str,
    model: str,
    worktree_path: str,
    pid: int | None = None,
    db_path: Path = DB_PATH,
) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO agents (id, session_id, role, model, worktree_path, pid) VALUES (?,?,?,?,?,?)",
            (agent_id, session_id, role, model, worktree_path, pid),
        )
        await conn.commit()


async def update_agent_status(
    agent_id: str, status: str, pid: int | None = None, db_path: Path = DB_PATH
) -> None:
    async with get_conn(db_path) as conn:
        if pid is not None:
            await conn.execute(
                "UPDATE agents SET status=?, pid=? WHERE id=?", (status, pid, agent_id)
            )
        else:
            await conn.execute(
                "UPDATE agents SET status=?, ended_at=datetime('now') WHERE id=?", (status, agent_id)
            )
        await conn.commit()


async def write_cost(
    session_id: str,
    agent_id: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    db_path: Path = DB_PATH,
) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute(
            "INSERT INTO cost_log (session_id, agent_id, input_tokens, output_tokens, cost_usd) VALUES (?,?,?,?,?)",
            (session_id, agent_id, input_tokens, output_tokens, cost_usd),
        )
        await conn.commit()



async def list_agents(session_id: str, db_path: Path = DB_PATH) -> list[dict]:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT id, session_id, role, model, status, worktree_path, started_at, ended_at "
            "FROM agents WHERE session_id=? ORDER BY started_at ASC",
            (session_id,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def list_sessions(limit: int = 20, db_path: Path = DB_PATH) -> list[dict]:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT id, name, path, type, status, created_at, last_active FROM sessions "
            "ORDER BY last_active DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_session(session_id: str, db_path: Path = DB_PATH) -> dict | None:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None
