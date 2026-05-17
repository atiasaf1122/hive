"""Tests for LangGraph worker execution and graph nodes."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.orchestrator.graph import _execute_worker
from backend.orchestrator.nodes.spawner import SpawnedAgent
from backend.persistence.db import init_db
from backend.persistence.events import create_session
from backend.workers.base import EventType, HiveEvent


def _mock_worker_events(*events: HiveEvent):
    async def _run(prompt, config):
        for e in events:
            yield e
    mock = MagicMock()
    mock.run = _run
    return mock


@pytest.mark.asyncio
async def test_execute_worker_success(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    await init_db(db)
    await create_session("sess-graph", db_path=db)

    events = [
        HiveEvent(type=EventType.AGENT_START, agent_id="ag1", session_id="sess-graph"),
        HiveEvent(type=EventType.TEXT_DELTA, agent_id="ag1", session_id="sess-graph", text="Hello!"),
        HiveEvent(type=EventType.COST, agent_id="ag1", session_id="sess-graph",
                  input_tokens=10, output_tokens=5, cost_usd=0.001),
        HiveEvent(type=EventType.AGENT_END, agent_id="ag1", session_id="sess-graph"),
    ]
    mock_worker = _mock_worker_events(*events)

    import backend.orchestrator.graph as gmod

    # Patch DB-writing functions to use our tmp db
    async def _w(e): pass
    async def _c(*a, **k): pass
    async def _ua(aid, status, pid=None): pass

    monkeypatch.setattr(gmod, "write_event", _w)
    monkeypatch.setattr(gmod, "write_cost", _c)
    monkeypatch.setattr(gmod, "update_agent_status", _ua)
    monkeypatch.setattr(
        "backend.orchestrator.graph.ClaudeCLIWorker", lambda: mock_worker
    )

    agent = SpawnedAgent(
        agent_id="ag1", role="Builder", model="claude:sonnet", worktree_path="/tmp"
    )
    result = await _execute_worker(agent, "do something", "sess-graph", 5)

    assert result["status"] == "completed"
    assert result["text_output"] == "Hello!"
    assert result["input_tokens"] == 10
    assert result["cost_usd"] == 0.001


@pytest.mark.asyncio
async def test_execute_worker_error_event(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    await init_db(db)
    await create_session("sess-err", db_path=db)

    events = [
        HiveEvent(type=EventType.AGENT_ERROR, agent_id="ag2", session_id="sess-err",
                  error="something went wrong"),
    ]
    mock_worker = _mock_worker_events(*events)

    import backend.orchestrator.graph as gmod

    async def _noop(*a, **k): pass
    monkeypatch.setattr(gmod, "write_event", _noop)
    monkeypatch.setattr(gmod, "write_cost", _noop)
    monkeypatch.setattr(gmod, "update_agent_status", _noop)
    monkeypatch.setattr("backend.orchestrator.graph.ClaudeCLIWorker", lambda: mock_worker)

    agent = SpawnedAgent(
        agent_id="ag2", role="Builder", model="claude:sonnet", worktree_path="/tmp"
    )
    result = await _execute_worker(agent, "fail task", "sess-err", 5)

    assert result["status"] == "failed"
    assert "something went wrong" in result["error"]
