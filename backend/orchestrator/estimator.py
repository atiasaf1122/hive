"""Pre-flight cost/duration estimate (D6).

Median + p90 of past sessions with a similar shape (active agent count
±1 — the dominant cost driver). Cold start returns None: "no estimate
yet" beats invented numbers. Estimate-vs-actual is recorded as an event
so the estimator's own quality is measurable.

Deviation noted: the spec's semantic-similarity tiebreaker is skipped
("if easy" — it isn't: request text isn't reliably joinable to per-session
cost shape yet); feature match only.
"""
from __future__ import annotations

import logging
import statistics
from pathlib import Path

from backend.persistence.db import DB_PATH, get_conn

logger = logging.getLogger(__name__)

MIN_SIMILAR_SESSIONS = 3


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[idx]


async def estimate_plan(composition: dict, db_path: Path = DB_PATH) -> dict | None:
    """Estimate (cost, duration) for a plan from similar past sessions."""
    active = [m for m in composition.get("team", []) if not m.get("passive")]
    agent_count = len(active)
    if agent_count == 0:
        return None

    try:
        async with get_conn(db_path) as conn:
            # Shape + outcome per past session: agent count, total cost,
            # wall duration from the event stream's first/last timestamp.
            # Subqueries, not JOINs — a JOIN would fan out cost rows per
            # agent row and double-count.
            cursor = await conn.execute(
                """
                SELECT a.session_id,
                       COUNT(a.id) AS agents,
                       (SELECT COALESCE(SUM(cost_usd), 0) FROM cost_log c
                        WHERE c.session_id = a.session_id)  AS cost,
                       (SELECT MAX(ts) - MIN(ts) FROM events e
                        WHERE e.session_id = a.session_id)  AS duration_s
                FROM agents a
                GROUP BY a.session_id
                """
            )
            rows = await cursor.fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Estimator query failed: %s", exc)
        return None

    similar = [
        (float(r["cost"]), float(r["duration_s"] or 0))
        for r in rows
        if abs(int(r["agents"]) - agent_count) <= 1
        and float(r["cost"]) > 0
        and (r["duration_s"] or 0) > 0
    ]
    if len(similar) < MIN_SIMILAR_SESSIONS:
        return None   # cold start: no estimate beats an invented one

    costs = [c for c, _ in similar]
    durations = [d for _, d in similar]
    return {
        "cost_median_usd": round(statistics.median(costs), 2),
        "cost_p90_usd": round(_percentile(costs, 90), 2),
        "duration_median_s": round(statistics.median(durations)),
        "duration_p90_s": round(_percentile(durations, 90)),
        "based_on_sessions": len(similar),
    }
