"""Tests for SQLite persistence layer — db, events, recovery."""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

import pytest

from backend.persistence.db import init_db, get_conn
from backend.persistence.events import (
    create_session,
    create_agent,
    get_session,
    get_session_events,
    list_sessions,
    update_agent_status,
    update_session_status,
    write_cost,
    write_event,
)
from backend.persistence.recovery import detect_crashed_agents, mark_agents_crashed
from backend.workers.base import EventType, HiveEvent


@pytest.fixture
async def tmp_db(tmp_path):
    """Provide a fresh temp DB path for each test."""
    db = tmp_path / "test.db"
    await init_db(db)
    return db


@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_db):
    async with get_conn(tmp_db) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {r[0] for r in await cursor.fetchall()}
    assert {"sessions", "agents", "events", "cost_log"}.issubset(tables)


@pytest.mark.asyncio
async def test_create_and_get_session(tmp_db):
    await create_session("sess1", name="test session", path="/tmp", db_path=tmp_db)
    row = await get_session("sess1", db_path=tmp_db)
    assert row is not None
    assert row["id"] == "sess1"
    assert row["name"] == "test session"
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_list_sessions(tmp_db):
    await create_session("s1", name="first", db_path=tmp_db)
    await create_session("s2", name="second", db_path=tmp_db)
    rows = await list_sessions(db_path=tmp_db)
    ids = [r["id"] for r in rows]
    assert "s1" in ids
    assert "s2" in ids


@pytest.mark.asyncio
async def test_update_session_status(tmp_db):
    await create_session("sess2", db_path=tmp_db)
    await update_session_status("sess2", "completed", db_path=tmp_db)
    row = await get_session("sess2", db_path=tmp_db)
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_write_and_read_event(tmp_db):
    await create_session("sess3", db_path=tmp_db)
    event = HiveEvent(
        type=EventType.TEXT_DELTA,
        agent_id="agent1",
        session_id="sess3",
        text="hello world",
    )
    await write_event(event, path=tmp_db)
    events = await get_session_events("sess3", path=tmp_db)
    assert len(events) == 1
    assert events[0]["type"] == EventType.TEXT_DELTA
    assert events[0]["payload"]["text"] == "hello world"


@pytest.mark.asyncio
async def test_multiple_events_in_order(tmp_db):
    await create_session("sess4", db_path=tmp_db)
    for i, text in enumerate(["a", "b", "c"]):
        e = HiveEvent(type=EventType.TEXT_DELTA, agent_id="ag", session_id="sess4", text=text, ts=float(i))
        await write_event(e, path=tmp_db)
    events = await get_session_events("sess4", path=tmp_db)
    assert [ev["payload"]["text"] for ev in events] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_write_cost(tmp_db):
    await create_session("sess5", db_path=tmp_db)
    await write_cost("sess5", "ag1", 100, 50, 0.0012, db_path=tmp_db)
    async with get_conn(tmp_db) as conn:
        cursor = await conn.execute("SELECT * FROM cost_log WHERE session_id='sess5'")
        row = await cursor.fetchone()
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50
    assert abs(row["cost_usd"] - 0.0012) < 1e-6


@pytest.mark.asyncio
async def test_recovery_detects_dead_pid(tmp_db):
    await create_session("sess6", db_path=tmp_db)
    await create_agent("ag1", "sess6", "worker", "claude:sonnet", "/tmp", pid=999999, db_path=tmp_db)

    crashed = await detect_crashed_agents(db_path=tmp_db)
    ids = [a["id"] for a in crashed]
    assert "ag1" in ids


@pytest.mark.asyncio
async def test_recovery_does_not_flag_live_pid(tmp_db):
    await create_session("sess7", db_path=tmp_db)
    # Use current process PID — guaranteed to be alive
    await create_agent("ag2", "sess7", "worker", "claude:sonnet", "/tmp", pid=os.getpid(), db_path=tmp_db)

    crashed = await detect_crashed_agents(db_path=tmp_db)
    ids = [a["id"] for a in crashed]
    assert "ag2" not in ids


@pytest.mark.asyncio
async def test_mark_agents_crashed_updates_status(tmp_db):
    await create_session("sess8", db_path=tmp_db)
    await create_agent("ag3", "sess8", "worker", "claude:sonnet", "/tmp", pid=999999, db_path=tmp_db)
    await mark_agents_crashed(["ag3"], db_path=tmp_db)

    async with get_conn(tmp_db) as conn:
        cursor = await conn.execute("SELECT status FROM agents WHERE id='ag3'")
        row = await cursor.fetchone()
    assert row["status"] == "crashed"


@pytest.mark.asyncio
async def test_init_db_idempotent(tmp_db):
    """Calling init_db twice should not raise or corrupt the schema."""
    await init_db(tmp_db)
    await init_db(tmp_db)
    row = await get_session("nonexistent", db_path=tmp_db)
    assert row is None


# ── delete_session_data (hard delete, Phase A) ─────────────────────────────


@pytest.mark.asyncio
async def test_delete_session_data_removes_everything(tmp_db):
    from backend.persistence.events import delete_session_data

    await create_session("del1", name="doomed", db_path=tmp_db)
    await create_agent("dag1", "del1", role="Builder", model="claude:sonnet",
                       worktree_path="/tmp/dag1", db_path=tmp_db)
    await write_event(HiveEvent(
        type=EventType.TEXT_DONE, agent_id="dag1", session_id="del1", text="hi",
    ), path=tmp_db)
    await write_cost("del1", "dag1", 10, 20, 0.01, db_path=tmp_db)

    assert await delete_session_data("del1", db_path=tmp_db) is True

    async with get_conn(tmp_db) as conn:
        for table in ("sessions", "agents", "events", "cost_log"):
            col = "id" if table == "sessions" else (
                "id" if table == "agents" else "session_id")
            key = "del1" if table in ("sessions", "events", "cost_log") else "dag1"
            cursor = await conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {col}=?", (key,))
            row = await cursor.fetchone()
            assert row["n"] == 0, f"{table} not cleaned"


@pytest.mark.asyncio
async def test_delete_session_data_missing_returns_false(tmp_db):
    from backend.persistence.events import delete_session_data

    assert await delete_session_data("ghost", db_path=tmp_db) is False


@pytest.mark.asyncio
async def test_delete_session_data_leaves_other_sessions(tmp_db):
    from backend.persistence.events import delete_session_data

    await create_session("keep", db_path=tmp_db)
    await create_session("drop", db_path=tmp_db)
    await delete_session_data("drop", db_path=tmp_db)

    assert await get_session("keep", db_path=tmp_db) is not None
    assert await get_session("drop", db_path=tmp_db) is None
