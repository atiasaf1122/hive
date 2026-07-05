"""Lifecycle endpoints — small surface the desktop shell uses to decide
what's safe to shut down when the user closes the window.

Phase 9C ships:
    GET /api/lifecycle/active-counts
        → { interactive_agents, enabled_automations, telegram_bot_running }

The desktop side will use these in Phase 9D to:
  - Confirm before close when interactive agents are running ("Stop and close?").
  - Decide whether to keep the backend alive in the tray (any enabled
    automation or running bot = keep running).
  - Render a tray badge with the live automation count.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from backend.persistence.db import DB_PATH, get_conn

router = APIRouter(prefix="/api/lifecycle")


async def _interactive_agent_count(db_path: Path) -> int:
    """Agents currently marked active (running interactive work)."""
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM agents WHERE status = 'active'"
        )
        row = await cursor.fetchone()
    return int(row["n"] or 0)


async def _enabled_automation_count(db_path: Path) -> int:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM pipelines WHERE enabled = 1"
        )
        row = await cursor.fetchone()
    return int(row["n"] or 0)


@router.get("/active-counts")
async def active_counts() -> dict:
    """Snapshot of things that may legitimately keep the backend alive."""
    interactive = await _interactive_agent_count(DB_PATH)
    automations = await _enabled_automation_count(DB_PATH)
    return {
        "interactive_agents": interactive,
        "enabled_automations": automations,
        # Telegram is parked (Phase A) — the bot never runs with the backend.
        "telegram_bot_running": False,
        # Convenience flags so the UI doesn't recompute the same logic.
        "has_interactive_work": interactive > 0,
        "should_keep_background": automations > 0,
    }
