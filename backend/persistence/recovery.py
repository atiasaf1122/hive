"""Startup recovery — detect agents that crashed while marked 'active'.

On every HIVE startup we query agents with status='active' and check
if their recorded PID still exists. If not, they crashed and we mark
them accordingly. The user is then prompted to resume or discard.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from backend.persistence.db import DB_PATH, get_conn

logger = logging.getLogger(__name__)


async def detect_crashed_agents(db_path: Path = DB_PATH) -> list[dict]:
    """Return agents that were active when the backend last stopped."""
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT a.id, a.session_id, a.role, a.model, a.pid, a.started_at, "
            "       s.name as session_name "
            "FROM agents a JOIN sessions s ON a.session_id = s.id "
            "WHERE a.status = 'active'",
        )
        rows = await cursor.fetchall()

    crashed = []
    for row in rows:
        agent = dict(row)
        pid = agent.get("pid")
        if pid and not _pid_alive(pid):
            crashed.append(agent)
        elif pid is None:
            # No PID recorded — treat as crashed
            crashed.append(agent)
    return crashed


async def mark_agents_crashed(agent_ids: list[str], db_path: Path = DB_PATH) -> None:
    """Mark a list of agents as crashed in the DB.

    Deliberately does NOT touch the sessions table: a session whose agents
    died is still resumable from its LangGraph checkpoint, so it becomes
    'idle' via `reconcile_idle_sessions` rather than 'failed'. 'failed' is
    reserved for runtime runner exceptions (see api/http.py).
    """
    if not agent_ids:
        return
    async with get_conn(db_path) as conn:
        await conn.executemany(
            "UPDATE agents SET status='crashed', ended_at=datetime('now') WHERE id=?",
            [(aid,) for aid in agent_ids],
        )
        await conn.commit()
    logger.info("Marked %d agent(s) as crashed: %s", len(agent_ids), agent_ids)


async def reconcile_idle_sessions(db_path: Path = DB_PATH) -> int:
    """Move parked-but-orphaned sessions from 'active' to 'idle'.

    Runs at startup, when no session runner exists yet by definition. Any
    session still marked 'active' with no live agent rows is a conversation
    parked between turns whose backend went away — it is resumable, not
    running. Before this pass existed such sessions stayed 'active' forever
    (the stuck-sessions bug): recovery only reached sessions transitively
    through crashed agents and never scanned the sessions table itself.

    Idempotent: already-'idle' rows don't match the WHERE clause, and
    'closed'/'failed' sessions are never touched. Deliberately does not
    bump last_active — reconciliation is bookkeeping, not user activity.
    """
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "UPDATE sessions SET status='idle' "
            "WHERE status='active' "
            "AND id NOT IN (SELECT session_id FROM agents WHERE status='active')"
        )
        await conn.commit()
        return cursor.rowcount


async def expire_pending_approvals(db_path: Path = DB_PATH) -> int:
    """Mark pending_approvals rows belonging to non-active sessions as expired.

    What this does NOT do: blanket-expire every pending row. The previous
    behaviour blanket-expired everything, which silently destroyed approvals
    for sessions a user might re-open after the restart — until someone
    wires a "resume the LangGraph state" path that re-registers an in-memory
    Future, those approvals are unrecoverable but at least the DB still
    shows they were requested. Only rows belonging to sessions whose status
    is already 'closed' / 'failed' / 'cancelled' are clearly orphaned.
    """
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "UPDATE pending_approvals SET status='expired', "
            "resolved_at=datetime('now') "
            "WHERE status='pending' "
            "AND session_id IN (SELECT id FROM sessions WHERE status != 'active')"
        )
        await conn.commit()
        return cursor.rowcount


async def run_startup_recovery(db_path: Path = DB_PATH) -> list[dict]:
    """Full recovery pass: detect crashed agents, mark them, return list."""
    crashed = await detect_crashed_agents(db_path)
    if crashed:
        agent_ids = [a["id"] for a in crashed]
        await mark_agents_crashed(agent_ids, db_path)
        logger.warning(
            "Recovered %d crashed agent(s) from previous session", len(crashed)
        )

    idled = await reconcile_idle_sessions(db_path)
    if idled:
        logger.warning(
            "Reconciled %d orphaned 'active' session(s) to 'idle'", idled
        )

    # Runs AFTER idle reconciliation on purpose: approvals belonging to
    # now-idle sessions are stale (a resumed runner re-registers a fresh
    # correlation id for any replayed interrupt), so they should expire.
    expired = await expire_pending_approvals(db_path)
    if expired:
        logger.warning(
            "Expired %d pending approval(s) left over from previous backend process",
            expired,
        )
    return crashed


def _pid_alive(pid: int) -> bool:
    """Check if a PID is still running on this machine."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
