"""Honest usage telemetry for the Usage tab.

Claude Max doesn't expose its rate-limit quota anywhere — there's no API.
We can only show what we observe locally:

    * input/output tokens consumed by *this* HIVE install
    * recent burn rate (last hour vs 7-day average)
    * count of `system/api_retry` events recorded (Anthropic's "rate limit
      hit, retrying" signal — Phase 0 invariant #6)
    * Ollama runs we made (count by model)

The frontend renders three sections (Claude / External API / Ollama)
with explicit caveats so users aren't misled into reading a fictional
"quota left" number.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from backend.persistence.db import DB_PATH, get_conn

router = APIRouter(prefix="/api/usage")


class WindowUsage(BaseModel):
    label: str
    hours: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ClaudeUsage(BaseModel):
    last_hour: WindowUsage
    last_5h: WindowUsage
    last_7d: WindowUsage
    rate_limit_hits_week: int
    burn_ratio: float  # last-hour rate / 7-day-average hourly rate; >2 ⇒ hot


class OllamaUsage(BaseModel):
    total_runs_week: int
    by_model: list[dict]


class UsageResponse(BaseModel):
    claude: ClaudeUsage
    ollama: OllamaUsage
    notes: list[str]


async def _window(hours: int, db_path: Path) -> WindowUsage:
    async with get_conn(db_path) as conn:
        cur = await conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0) AS itok, "
            "       COALESCE(SUM(output_tokens),0) AS otok, "
            "       COALESCE(SUM(cost_usd),0)     AS cost "
            "FROM cost_log WHERE ts >= datetime('now', ?)",
            (f"-{hours} hours",),
        )
        row = await cur.fetchone()
    return WindowUsage(
        label=f"last {hours}h" if hours < 168 else f"last {hours // 24}d",
        hours=hours,
        input_tokens=int(row["itok"] or 0),
        output_tokens=int(row["otok"] or 0),
        cost_usd=float(row["cost"] or 0.0),
    )


async def _rate_limit_hits_week(db_path: Path) -> int:
    async with get_conn(db_path) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) AS n FROM events "
            "WHERE type='system/rate_limit' "
            "AND ts >= (strftime('%s','now') - 7*86400)"
        )
        row = await cur.fetchone()
    return int(row["n"] or 0)


async def _ollama_usage_week(db_path: Path) -> OllamaUsage:
    async with get_conn(db_path) as conn:
        cur = await conn.execute(
            "SELECT a.model AS model, COUNT(*) AS runs "
            "FROM agents a WHERE a.model LIKE 'ollama:%' "
            "AND a.started_at >= datetime('now', '-7 days') "
            "GROUP BY a.model ORDER BY runs DESC",
        )
        rows = await cur.fetchall()
    by_model = [{"model": r["model"], "runs": int(r["runs"])} for r in rows]
    total = sum(r["runs"] for r in by_model)
    return OllamaUsage(total_runs_week=total, by_model=by_model)


@router.get("/summary", response_model=UsageResponse)
async def usage_summary() -> UsageResponse:
    last_hour = await _window(1, DB_PATH)
    last_5h = await _window(5, DB_PATH)
    last_7d = await _window(24 * 7, DB_PATH)

    week_total = last_7d.input_tokens + last_7d.output_tokens
    week_hours = 24 * 7
    avg_per_hour = week_total / week_hours if week_total else 0.0
    last_hour_total = last_hour.input_tokens + last_hour.output_tokens
    burn = (last_hour_total / avg_per_hour) if avg_per_hour > 0 else 0.0

    hits = await _rate_limit_hits_week(DB_PATH)
    ollama = await _ollama_usage_week(DB_PATH)

    notes = [
        "Anthropic doesn't expose Max-subscription quotas — these numbers are "
        "derived from your local activity, not from Anthropic's billing.",
        "Burn ratio compares the last hour to your 7-day hourly average. "
        "Above 2× is unusually busy.",
    ]

    return UsageResponse(
        claude=ClaudeUsage(
            last_hour=last_hour,
            last_5h=last_5h,
            last_7d=last_7d,
            rate_limit_hits_week=hits,
            burn_ratio=round(burn, 2),
        ),
        ollama=ollama,
        notes=notes,
    )
