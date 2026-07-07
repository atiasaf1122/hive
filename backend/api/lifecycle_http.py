"""Lifecycle endpoints — small surface the desktop shell uses to decide
what's safe to shut down when the user closes the window.

    GET /api/lifecycle/active-counts
        → { interactive_agents, enabled_automations, telegram_bot_running }

    POST /api/lifecycle/shutdown   (post-1.0 Part 6)
        → hermetic teardown: kill orphaned swarm workers (the narrow
          stream-json + skip-permissions pattern — NEVER interactive
          claude sessions), then exit the backend gracefully. Called by
          the Tauri shell when the window's X confirms close. Never runs
          `wsl --shutdown` — that stays exclusive to the Stop script.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from pathlib import Path

from fastapi import APIRouter

from backend.persistence.db import DB_PATH, get_conn

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/lifecycle")

# Single source for the worker kill pattern is scripts/stop-hive-wsl.sh —
# the shutdown endpoint INVOKES it (--workers-only) so the pattern isn't
# duplicated. This constant is only the fallback for a backend running
# outside the repo checkout.
_WORKER_KILL_PATTERN = (
    r"claude.*--output-format stream-json.*--dangerously-skip-permissions"
)
_STOP_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "stop-hive-wsl.sh"


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


def _kill_orphaned_workers() -> list[str]:
    """Kill orphaned swarm workers. Returns human-readable report lines."""
    if _STOP_SCRIPT.exists():
        try:
            proc = subprocess.run(
                ["bash", str(_STOP_SCRIPT), "--workers-only"],
                capture_output=True, text=True, timeout=30,
            )
            return [ln for ln in proc.stdout.splitlines() if ln.strip()]
        except Exception as exc:  # noqa: BLE001 — fall through to native kill
            logger.warning("stop-hive-wsl.sh --workers-only failed: %s", exc)
    lines: list[str] = []
    try:
        out = subprocess.run(
            ["pgrep", "-af", _WORKER_KILL_PATTERN],
            capture_output=True, text=True, timeout=10,
        ).stdout
        for row in out.splitlines():
            pid, _, cmd = row.partition(" ")
            # Real worker argv never contains a literal ".*" — skip tooling
            # that merely mentions the pattern (same guard as the script).
            if ".*" in cmd:
                continue
            try:
                os.kill(int(pid), signal.SIGTERM)
                lines.append(f"killed worker pid {pid}")
            except (ProcessLookupError, ValueError):
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("Native worker kill failed: %s", exc)
    return lines or ["(no orphaned workers running)"]


def _schedule_exit() -> None:
    """Respond first, then die: SIGTERM for a graceful uvicorn shutdown,
    hard exit as the backstop if open WebSockets stall the drain."""
    loop = asyncio.get_running_loop()
    loop.call_later(0.5, os.kill, os.getpid(), signal.SIGTERM)
    loop.call_later(6.0, os._exit, 0)


@router.post("/shutdown")
async def shutdown() -> dict:
    """Hermetic shutdown for the window's X — workers, then the backend."""
    report = await asyncio.to_thread(_kill_orphaned_workers)
    logger.info("Lifecycle shutdown requested — %s", "; ".join(report))
    _schedule_exit()
    return {"ok": True, "workers": report, "backend": "exiting"}


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
