"""Cost-dashboard aggregation tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from backend.api.cost_http import aggregate_cost_summary
from backend.main import app
from backend.persistence.db import get_conn, init_db
from backend.persistence.events import create_session, write_cost


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    path = tmp_path / "test.db"
    await init_db(path)
    return path


async def _insert_cost(db_path: Path, session_id: str, cost: float, days_ago: int = 0) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute(
            "INSERT INTO cost_log (ts, session_id, agent_id, input_tokens, output_tokens, cost_usd) "
            "VALUES (datetime('now', ?), ?, ?, ?, ?, ?)",
            (f"-{days_ago} days", session_id, "ag", 100, 50, cost),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_summary_empty(db: Path) -> None:
    summary = await aggregate_cost_summary(days=7, db_path=db)
    assert summary.total_cost_usd == 0.0
    assert summary.by_session == []
    assert summary.by_day == []


@pytest.mark.asyncio
async def test_summary_aggregates_total_and_tokens(db: Path) -> None:
    await create_session("s1", db_path=db)
    await write_cost("s1", "ag", 100, 50, 0.001, db_path=db)
    await write_cost("s1", "ag", 200, 80, 0.002, db_path=db)

    summary = await aggregate_cost_summary(days=7, db_path=db)
    assert summary.total_cost_usd == pytest.approx(0.003, rel=1e-6)
    assert summary.total_input_tokens == 300
    assert summary.total_output_tokens == 130


@pytest.mark.asyncio
async def test_summary_sorts_sessions_by_cost(db: Path) -> None:
    await create_session("cheap", db_path=db)
    await create_session("expensive", db_path=db)
    await write_cost("cheap", "ag", 100, 50, 0.001, db_path=db)
    await write_cost("expensive", "ag", 1000, 500, 0.05, db_path=db)

    summary = await aggregate_cost_summary(days=7, db_path=db)
    assert len(summary.by_session) == 2
    assert summary.by_session[0].session_id == "expensive"
    assert summary.by_session[0].cost_usd == pytest.approx(0.05)
    assert summary.by_session[1].session_id == "cheap"


@pytest.mark.asyncio
async def test_summary_window_clips_older_rows(db: Path) -> None:
    """Cost rows older than `days` should not appear."""
    await create_session("s1", db_path=db)
    await _insert_cost(db, "s1", 0.99, days_ago=30)  # outside 7-day window
    await write_cost("s1", "ag", 10, 5, 0.001, db_path=db)  # inside

    summary = await aggregate_cost_summary(days=7, db_path=db)
    assert summary.total_cost_usd == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_summary_top_n_sessions_limit(db: Path) -> None:
    for i in range(10):
        sid = f"s{i:02}"
        await create_session(sid, db_path=db)
        await write_cost(sid, "ag", 10, 5, 0.001 * (i + 1), db_path=db)

    summary = await aggregate_cost_summary(days=7, top_n_sessions=3, db_path=db)
    assert len(summary.by_session) == 3
    # Highest is s09 at 0.010
    assert summary.by_session[0].session_id == "s09"


def test_summary_endpoint_via_fastapi() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/cost/summary?days=1")
    assert resp.status_code == 200
    body = resp.json()
    # Just structural assertions — content depends on the user's real DB.
    assert "total_cost_usd" in body
    assert "by_session" in body
    assert "by_day" in body
    assert body["days"] == 1


def test_summary_days_clamped() -> None:
    """days=0 should be clamped to ≥1, days=9999 to ≤365."""
    with TestClient(app) as client:
        zero = client.get("/api/cost/summary?days=0").json()
        big = client.get("/api/cost/summary?days=9999").json()
    assert zero["days"] == 1
    assert big["days"] == 365
