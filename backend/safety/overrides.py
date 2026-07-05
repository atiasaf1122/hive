"""Per-session safety overrides.

Each session can tighten (or, with explicit awareness, loosen) any of
the build-time hard stops. A row in `session_safety_overrides` is the
source of truth; absent fields inherit `HARD_STOPS` from
`backend.safety.hard_stops`.

`effective_limits(session_id)` is the function the orchestrator calls
right before `check_hard_stops()` in `spawn_node` — it merges the
override row with the defaults and returns a fully-populated
`HardStops` dataclass.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from backend.persistence.db import DB_PATH, get_conn
from backend.safety.hard_stops import DEFAULTS, HardStops


@dataclass
class SafetyOverride:
    """The user-visible per-project knobs. None = inherit default."""
    max_tokens_per_autonomous_run: int | None = None
    max_session_duration_hours: float | None = None
    max_concurrent_agents: int | None = None
    max_same_file_edits: int | None = None

    def to_dict(self) -> dict:
        return {
            "max_tokens_per_autonomous_run": self.max_tokens_per_autonomous_run,
            "max_session_duration_hours": self.max_session_duration_hours,
            "max_concurrent_agents": self.max_concurrent_agents,
            "max_same_file_edits": self.max_same_file_edits,
        }


def merge(defaults: HardStops, override: SafetyOverride) -> HardStops:
    """Return a HardStops where any non-None override field replaces the default."""
    merged = replace(defaults)
    if override.max_tokens_per_autonomous_run is not None:
        merged = replace(merged, max_tokens_per_autonomous_run=int(override.max_tokens_per_autonomous_run))
    if override.max_session_duration_hours is not None:
        merged = replace(merged, max_session_duration_hours=float(override.max_session_duration_hours))
    if override.max_concurrent_agents is not None:
        merged = replace(merged, max_concurrent_agents=int(override.max_concurrent_agents))
    if override.max_same_file_edits is not None:
        merged = replace(merged, max_same_file_edits=int(override.max_same_file_edits))
    return merged


async def load_override(
    session_id: str,
    db_path: Path = DB_PATH,
) -> SafetyOverride:
    """Read the saved overrides for a session. Missing row → empty override."""
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM session_safety_overrides WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return SafetyOverride()
    return SafetyOverride(
        max_tokens_per_autonomous_run=row["max_tokens_per_autonomous_run"],
        max_session_duration_hours=row["max_session_duration_hours"],
        max_concurrent_agents=row["max_concurrent_agents"],
        max_same_file_edits=row["max_same_file_edits"],
    )


async def save_override(
    session_id: str,
    override: SafetyOverride,
    db_path: Path = DB_PATH,
) -> None:
    """Upsert the overrides row."""
    async with get_conn(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO session_safety_overrides (
                session_id,
                max_tokens_per_autonomous_run, max_session_duration_hours,
                max_concurrent_agents, max_same_file_edits,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(session_id) DO UPDATE SET
                max_tokens_per_autonomous_run = excluded.max_tokens_per_autonomous_run,
                max_session_duration_hours    = excluded.max_session_duration_hours,
                max_concurrent_agents         = excluded.max_concurrent_agents,
                max_same_file_edits           = excluded.max_same_file_edits,
                updated_at                    = datetime('now')
            """,
            (
                session_id,
                override.max_tokens_per_autonomous_run,
                override.max_session_duration_hours,
                override.max_concurrent_agents,
                override.max_same_file_edits,
            ),
        )
        await conn.commit()


async def clear_override(session_id: str, db_path: Path = DB_PATH) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute(
            "DELETE FROM session_safety_overrides WHERE session_id = ?",
            (session_id,),
        )
        await conn.commit()


async def effective_limits(
    session_id: str,
    *,
    defaults: HardStops = DEFAULTS,
    db_path: Path = DB_PATH,
) -> HardStops:
    """The function `spawn_node` calls. Merges saved override with defaults."""
    override = await load_override(session_id, db_path=db_path)
    return merge(defaults, override)
