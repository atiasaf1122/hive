"""Cost dashboard endpoint — aggregates cost_log + sessions for the UI."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from backend.persistence.db import DB_PATH, get_conn

router = APIRouter(prefix="/api/cost")


class SessionCost(BaseModel):
    session_id: str
    name: str
    cost_usd: float
    input_tokens: int
    output_tokens: int


class DailyCost(BaseModel):
    date: str
    cost_usd: float


class CostSummary(BaseModel):
    days: int
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    by_session: list[SessionCost]
    by_day: list[DailyCost]


async def aggregate_cost_summary(
    days: int = 7,
    top_n_sessions: int = 5,
    db_path: Path = DB_PATH,
) -> CostSummary:
    """Compute totals + per-session + per-day cost buckets for the last `days` days."""
    days = max(1, min(int(days), 365))

    async with get_conn(db_path) as conn:
        totals_cursor = await conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS cost,"
            "       COALESCE(SUM(input_tokens), 0) AS input_tokens,"
            "       COALESCE(SUM(output_tokens), 0) AS output_tokens "
            "FROM cost_log WHERE ts >= datetime('now', ?)",
            (f"-{days} days",),
        )
        totals_row = await totals_cursor.fetchone()

        session_cursor = await conn.execute(
            "SELECT c.session_id AS session_id, "
            "       COALESCE(s.name, c.session_id) AS name, "
            "       SUM(c.cost_usd) AS cost_usd, "
            "       SUM(c.input_tokens) AS input_tokens, "
            "       SUM(c.output_tokens) AS output_tokens "
            "FROM cost_log c LEFT JOIN sessions s ON s.id = c.session_id "
            "WHERE c.ts >= datetime('now', ?) "
            "GROUP BY c.session_id "
            "ORDER BY cost_usd DESC LIMIT ?",
            (f"-{days} days", top_n_sessions),
        )
        session_rows = await session_cursor.fetchall()

        daily_cursor = await conn.execute(
            "SELECT DATE(ts) AS date, SUM(cost_usd) AS cost_usd "
            "FROM cost_log WHERE ts >= datetime('now', ?) "
            "GROUP BY DATE(ts) ORDER BY date ASC",
            (f"-{days} days",),
        )
        daily_rows = await daily_cursor.fetchall()

    return CostSummary(
        days=days,
        total_cost_usd=float(totals_row["cost"] or 0.0),
        total_input_tokens=int(totals_row["input_tokens"] or 0),
        total_output_tokens=int(totals_row["output_tokens"] or 0),
        by_session=[
            SessionCost(
                session_id=r["session_id"],
                name=(r["name"] or "")[:60],
                cost_usd=float(r["cost_usd"] or 0.0),
                input_tokens=int(r["input_tokens"] or 0),
                output_tokens=int(r["output_tokens"] or 0),
            )
            for r in session_rows
        ],
        by_day=[
            DailyCost(date=r["date"], cost_usd=float(r["cost_usd"] or 0.0))
            for r in daily_rows
        ],
    )


@router.get("/summary", response_model=CostSummary)
async def cost_summary_endpoint(days: int = 7) -> CostSummary:
    """Cost dashboard data — totals, top 5 sessions, daily breakdown."""
    return await aggregate_cost_summary(days=days)


# ── F0.2: per-session breakdown by role ──────────────────────────────────────

class RoleCost(BaseModel):
    role: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    calls: int
    local: bool


class SessionCostBreakdown(BaseModel):
    session_id: str
    total_usd: float
    saved_via_local_usd: float     # local tokens priced at Haiku ($1/$5 MTok)
    by_role: list[RoleCost]


# agent_id prefixes → role buckets. Order matters (first match wins);
# everything unmatched is a worker.
_ROLE_PREFIXES = [
    ("planner-", "planner"),
    ("plan-gate-", "plan gate"),
    ("shape-classifier-", "task-shape classifier"),
    ("chat-", "chat"),
    ("summarizer-", "summarizer"),
    ("lesson-distiller-", "lesson distiller"),
    ("llm-review-", "llm review"),
    ("meta-", "meta"),
    ("compaction-", "compaction"),
]


def _role_of(agent_id: str) -> str:
    for prefix, role in _ROLE_PREFIXES:
        if agent_id.startswith(prefix):
            return role
    return "workers"


@router.get("/session/{session_id}", response_model=SessionCostBreakdown)
async def session_cost_breakdown(session_id: str) -> SessionCostBreakdown:
    """One session's spend by role — data already in cost_log (F0.2)."""
    async with get_conn(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT agent_id, input_tokens, output_tokens, cost_usd, local "
            "FROM cost_log WHERE session_id=?", (session_id,))
        rows = await cur.fetchall()

    buckets: dict[tuple[str, bool], dict] = {}
    saved = 0.0
    for r in rows:
        is_local = bool(r["local"])
        key = (_role_of(r["agent_id"]), is_local)
        b = buckets.setdefault(key, {"cost": 0.0, "itok": 0, "otok": 0, "n": 0})
        b["cost"] += float(r["cost_usd"] or 0.0)
        b["itok"] += int(r["input_tokens"] or 0)
        b["otok"] += int(r["output_tokens"] or 0)
        b["n"] += 1
        if is_local:
            saved += (r["input_tokens"] or 0) * 1.0 / 1e6 + (r["output_tokens"] or 0) * 5.0 / 1e6

    by_role = [
        RoleCost(role=role, local=is_local, cost_usd=round(b["cost"], 4),
                 input_tokens=b["itok"], output_tokens=b["otok"], calls=b["n"])
        for (role, is_local), b in sorted(
            buckets.items(), key=lambda kv: -kv[1]["cost"])
    ]
    return SessionCostBreakdown(
        session_id=session_id,
        total_usd=round(sum(b.cost_usd for b in by_role), 4),
        saved_via_local_usd=round(saved, 4),
        by_role=by_role,
    )
