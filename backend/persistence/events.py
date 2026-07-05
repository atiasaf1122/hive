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


async def get_or_create_claude_session(
    agent_id: str, db_path: Path = DB_PATH
) -> tuple[str, bool]:
    """Return (claude_session_uuid, resumed) for a logical agent.

    Agent ids are stable across turns within a HIVE session
    (`{role}-{session[:6]}-{index}`), so the first spawn mints a uuid the
    worker passes as `--session-id`, and every later spawn of the same
    logical agent gets (same uuid, resumed=True) → `--resume` keeps its
    conversation context instead of re-exploring the project. Persisted in
    agents.claude_session_id, so it survives backend restarts.
    """
    import uuid as _uuid

    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT claude_session_id FROM agents WHERE id=?", (agent_id,)
        )
        row = await cursor.fetchone()
        if row is not None and row["claude_session_id"]:
            return row["claude_session_id"], True

        new_id = str(_uuid.uuid4())
        await conn.execute(
            "UPDATE agents SET claude_session_id=? WHERE id=?", (new_id, agent_id)
        )
        await conn.commit()
        return new_id, False


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


async def delete_session_data(session_id: str, db_path: Path = DB_PATH) -> bool:
    """Hard-delete a session and everything keyed to it.

    Removes the session row plus agents, events, cost_log, pending
    approvals, safety overrides, and (best-effort) the LangGraph
    checkpoint rows for the thread. Returns False if the session
    didn't exist.
    """
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM sessions WHERE id=?", (session_id,)
        )
        if await cursor.fetchone() is None:
            return False

        for table, col in (
            ("events", "session_id"),
            ("cost_log", "session_id"),
            ("agents", "session_id"),
            ("pending_approvals", "session_id"),
            ("session_safety_overrides", "session_id"),
        ):
            await conn.execute(f"DELETE FROM {table} WHERE {col}=?", (session_id,))

        # LangGraph checkpointer tables share this DB file, keyed by
        # thread_id == session_id. They may not exist yet (fresh DB where
        # no graph ever ran), so each delete is best-effort.
        for table in (
            "checkpoints",
            "checkpoint_writes",
            "checkpoint_blobs",
        ):
            try:
                await conn.execute(
                    f"DELETE FROM {table} WHERE thread_id=?", (session_id,)
                )
            except aiosqlite.OperationalError:
                pass  # table doesn't exist — nothing to clean

        await conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        await conn.commit()
    return True


async def get_session(session_id: str, db_path: Path = DB_PATH) -> dict | None:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


# ── pending_approvals (invariant #5: correlation IDs survive restart) ──────


async def create_pending_approval(
    correlation_id: str,
    session_id: str,
    agent_id: str,
    request_payload: dict,
    db_path: Path = DB_PATH,
) -> None:
    """Persist an approval request before the runner awaits it. Read back on
    restart so re-opened sessions can re-emit the interrupt to the client."""
    async with get_conn(db_path) as conn:
        await conn.execute(
            "INSERT INTO pending_approvals "
            "(correlation_id, session_id, agent_id, request_payload, status) "
            "VALUES (?, ?, ?, ?, 'pending')",
            (correlation_id, session_id, agent_id, json.dumps(request_payload)),
        )
        await conn.commit()


async def resolve_pending_approval(
    correlation_id: str,
    status: str,
    response_payload: dict | None = None,
    db_path: Path = DB_PATH,
) -> bool:
    """Mark a pending_approvals row as resolved. Returns True iff the row was
    still pending (so callers can avoid double-firing waiters)."""
    if status not in {"approved", "rejected", "expired"}:
        raise ValueError(f"invalid resolution status: {status!r}")
    payload_json = json.dumps(response_payload) if response_payload is not None else None
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "UPDATE pending_approvals "
            "SET status=?, resolved_at=datetime('now'), response_payload=? "
            "WHERE correlation_id=? AND status='pending'",
            (status, payload_json, correlation_id),
        )
        await conn.commit()
        return cursor.rowcount > 0


async def get_pending_approval(
    correlation_id: str, db_path: Path = DB_PATH
) -> dict | None:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM pending_approvals WHERE correlation_id=?",
            (correlation_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    out = dict(row)
    if out.get("request_payload"):
        out["request_payload"] = json.loads(out["request_payload"])
    if out.get("response_payload"):
        out["response_payload"] = json.loads(out["response_payload"])
    return out


async def list_pending_approvals(
    session_id: str | None = None, db_path: Path = DB_PATH
) -> list[dict]:
    """List approval requests that are still awaiting a response.

    If `session_id` is given, restricts to that session; otherwise returns
    every pending row across the DB (used by restart recovery).
    """
    async with get_conn(db_path) as conn:
        if session_id is None:
            cursor = await conn.execute(
                "SELECT * FROM pending_approvals WHERE status='pending' "
                "ORDER BY created_at"
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM pending_approvals "
                "WHERE status='pending' AND session_id=? ORDER BY created_at",
                (session_id,),
            )
        rows = await cursor.fetchall()
    out = []
    for row in rows:
        d = dict(row)
        if d.get("request_payload"):
            d["request_payload"] = json.loads(d["request_payload"])
        if d.get("response_payload"):
            d["response_payload"] = json.loads(d["response_payload"])
        out.append(d)
    return out
