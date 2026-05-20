"""Lifecycle endpoint — used by Phase 9D's close-confirmation + tray."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from backend.api.lifecycle_http import (
    _enabled_automation_count,
    _interactive_agent_count,
)
from backend.main import app
from backend.persistence.db import get_conn, init_db
from backend.persistence.events import create_agent, create_session


@pytest_asyncio.fixture
async def db(tmp_path):
    path = tmp_path / "test.db"
    await init_db(path)
    return path


@pytest.mark.asyncio
async def test_interactive_count_zero_for_empty_db(db) -> None:
    assert await _interactive_agent_count(db) == 0


@pytest.mark.asyncio
async def test_interactive_count_picks_up_active_agents(db) -> None:
    await create_session("s1", db_path=db)
    await create_agent("a1", "s1", role="Builder", model="claude:sonnet",
                       worktree_path="/tmp/a1", db_path=db)
    assert await _interactive_agent_count(db) == 1

    async with get_conn(db) as conn:
        await conn.execute("UPDATE agents SET status='completed' WHERE id=?", ("a1",))
        await conn.commit()

    assert await _interactive_agent_count(db) == 0


@pytest.mark.asyncio
async def test_enabled_automation_count(db) -> None:
    async with get_conn(db) as conn:
        await conn.execute(
            "INSERT INTO pipelines (id, name, task, enabled) VALUES (?,?,?,?)",
            ("p1", "live", "a", 1),
        )
        await conn.execute(
            "INSERT INTO pipelines (id, name, task, enabled) VALUES (?,?,?,?)",
            ("p2", "paused", "b", 0),
        )
        await conn.commit()
    assert await _enabled_automation_count(db) == 1


def test_active_counts_endpoint_shape() -> None:
    with TestClient(app) as client:
        with patch("backend.api.lifecycle_http.get_bot", return_value=None):
            resp = client.get("/api/lifecycle/active-counts")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "interactive_agents",
        "enabled_automations",
        "telegram_bot_running",
        "has_interactive_work",
        "should_keep_background",
    ):
        assert key in body
    assert body["telegram_bot_running"] is False


def test_should_keep_background_when_bot_running() -> None:
    fake_bot = object()
    with TestClient(app) as client:
        with patch("backend.api.lifecycle_http.get_bot", return_value=fake_bot):
            resp = client.get("/api/lifecycle/active-counts")
    body = resp.json()
    assert body["telegram_bot_running"] is True
    assert body["should_keep_background"] is True
