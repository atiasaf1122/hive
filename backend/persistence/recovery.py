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
    """Mark a list of agents as crashed in the DB."""
    if not agent_ids:
        return
    async with get_conn(db_path) as conn:
        await conn.executemany(
            "UPDATE agents SET status='crashed', ended_at=datetime('now') WHERE id=?",
            [(aid,) for aid in agent_ids],
        )
        # Mark their sessions as failed if all agents in the session are done
        await conn.execute("""
            UPDATE sessions SET status='failed'
            WHERE id IN (
                SELECT DISTINCT session_id FROM agents WHERE id IN ({})
            )
            AND NOT EXISTS (
                SELECT 1 FROM agents
                WHERE session_id = sessions.id AND status = 'active'
            )
        """.format(",".join("?" * len(agent_ids))), agent_ids)
        await conn.commit()
    logger.info("Marked %d agent(s) as crashed: %s", len(agent_ids), agent_ids)


async def run_startup_recovery(db_path: Path = DB_PATH) -> list[dict]:
    """Full recovery pass: detect crashed agents, mark them, return list."""
    crashed = await detect_crashed_agents(db_path)
    if crashed:
        agent_ids = [a["id"] for a in crashed]
        await mark_agents_crashed(agent_ids, db_path)
        logger.warning(
            "Recovered %d crashed agent(s) from previous session", len(crashed)
        )
    return crashed


def _pid_alive(pid: int) -> bool:
    """Check if a PID is still running on this machine."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
