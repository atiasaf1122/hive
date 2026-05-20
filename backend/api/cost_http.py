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
