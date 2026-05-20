"""Bugs found dogfooding the backend before the next snake-app try.

Three independent fixes covered here:

  - Bug 1: planner ran with ``cwd=/tmp`` regardless of workspace.
  - Bug 2: ``create_session_endpoint`` persisted ``path=''`` in the DB
           even when the caller supplied a real workspace.
  - Bug 3: a hung orchestrator subprocess produced *no* events to the
           UI; the watchdog now emits ``orchestrator_stall_hint``
           after ``HIVE_ORCH_STALL_WARN_S`` seconds.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api import event_bus
from backend.api import http as http_mod
from backend.main import app
from backend.workers.base import EventType, HiveEvent, WorkerConfig


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    http_mod._pending_approvals.clear()
    http_mod._pending_inputs.clear()
    http_mod._running_tasks.clear()
    http_mod._message_queues.clear()


# ─── Bug 1: planner must NEVER run in the workspace ───────────────────────
#
# An earlier dogfood pass spawned the planner at cwd=project_path to give
# it read access to README / package.json when picking a team. That
# turned out to be unsafe: `claude --dangerously-skip-permissions` at
# the workspace cwd ignored the JSON-only instruction and built the
# project itself, leaving untracked files that then broke the Reviewer's
# merge. Until we can wire ``--allowed-tools`` to restrict the planner
# to read-only tools, the planner stays at /tmp regardless of the
# workspace.


@pytest.mark.asyncio
async def test_planner_runs_at_tmp_regardless_of_project_path(tmp_path) -> None:
    from backend.orchestrator.nodes import planner as planner_mod

    captured: dict = {}

    async def stub_run(self, prompt: str, config: WorkerConfig) -> AsyncIterator[HiveEvent]:
        captured["cwd"] = config.worktree_path
        yield HiveEvent(
            type=EventType.AGENT_END,
            agent_id=config.agent_id, session_id=config.session_id,
        )

    with patch.object(planner_mod.ClaudeCLIWorker, "run", stub_run):
        await planner_mod.orchestrate(
            message="hi", session_id="s-isolated",
            project_path=str(tmp_path),  # ignored on purpose
        )
    assert captured["cwd"] == "/tmp"


# ─── Bug 2: project_path must be persisted to the sessions table ──────────


@pytest.fixture(autouse=True)
def _stub_launch():
    """Keep the session runner a no-op for these tests."""
    with patch("backend.api.http.launch_session") as mocked:
        mocked.return_value = None
        yield mocked


def test_create_session_persists_project_path(tmp_path, client: TestClient) -> None:
    """Sessions previously stored path='' because the call site forgot to
    forward project_path to db_create_session."""
    resp = client.post("/api/sessions", json={
        "task": "do thing", "project_path": str(tmp_path),
        "approval_mode": "manual",
    })
    assert resp.status_code == 200
    sid = resp.json()["session_id"]

    import sqlite3

    from backend.persistence.db import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT path FROM sessions WHERE id=?", (sid,),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["path"] == str(tmp_path)


def test_create_session_default_path_is_recorded(client: TestClient) -> None:
    """No supplied path → backend builds ~/.hive/sessions/<id>/ and records it."""
    resp = client.post("/api/sessions", json={"task": "x"})
    assert resp.status_code == 200
    sid = resp.json()["session_id"]

    import sqlite3

    from backend.persistence.db import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT path FROM sessions WHERE id=?", (sid,),
    ).fetchone()
    conn.close()
    assert row is not None
    # Whatever path the backend picked, it must be non-empty.
    assert row["path"].strip() != ""
    assert sid in row["path"]


# ─── Bug 3: orchestrator stall watchdog ───────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_stall_hint_fires_when_silent(monkeypatch) -> None:
    """If the orchestrator hasn't streamed anything by the deadline, the
    runner must emit an ``orchestrator_stall_hint`` event so the UI can
    surface "still thinking…" rather than a silent void."""
    sid = "s-stall"
    event_bus.remove(sid)
    event_bus.get_or_create(sid)

    # Tighten the deadline so the test stays fast.
    monkeypatch.setenv("HIVE_ORCH_STALL_WARN_S", "0.05")

    # Stub run_session so it never emits and never returns; we only want
    # to observe the watchdog firing.
    async def never_returns(**kwargs):
        await asyncio.sleep(5)
        return None

    monkeypatch.setattr("backend.api.http.run_session", never_returns)

    runner = asyncio.create_task(http_mod._session_runner(
        session_id=sid, task="t", model="claude:sonnet",
        approval_mode="manual", project_path="/tmp", max_turns=1,
    ))
    try:
        # Wait past the watchdog deadline.
        await asyncio.sleep(0.4)
        hints = [
            e for e in event_bus.events_since(sid, 0)
            if e.get("type") == "orchestrator_stall_hint"
        ]
        assert hints, "watchdog must emit at least one stall hint"
        assert "claude --version" in hints[0]["hint"]
    finally:
        runner.cancel()
        try:
            await runner
        except (asyncio.CancelledError, Exception):
            pass
        event_bus.remove(sid)


@pytest.mark.asyncio
async def test_orchestrator_text_not_duplicated_with_text_done() -> None:
    """Regression: when both `stream_event` deltas AND the consolidated
    `assistant` message land, the orchestrator returned the same text
    twice (visible in chat as a duplicated paragraph)."""
    from backend.orchestrator.nodes import planner as planner_mod

    async def stub_run(self, prompt, config):
        # Partial deltas + the consolidated assistant message — both
        # contain the same logical text.
        for chunk in ('{"response":"', 'hi","team":[', ']}'):
            yield HiveEvent(
                type=EventType.TEXT_DELTA, text=chunk,
                agent_id=config.agent_id, session_id=config.session_id,
            )
        yield HiveEvent(
            type=EventType.TEXT_DONE, text='{"response":"hi","team":[]}',
            agent_id=config.agent_id, session_id=config.session_id,
        )
        yield HiveEvent(
            type=EventType.AGENT_END,
            agent_id=config.agent_id, session_id=config.session_id,
        )

    with patch.object(planner_mod.ClaudeCLIWorker, "run", stub_run):
        decision = await planner_mod.orchestrate(
            message="hi", session_id="dup-check", project_path="/tmp",
        )
    # Without the TEXT_DONE fix the response would be the JSON twice
    # concatenated — and json.loads would reject the result, dropping
    # back to the fallback team. The fix means we see exactly one
    # parsed decision.
    assert decision.response == "hi"


@pytest.mark.asyncio
async def test_stall_hint_silent_when_orchestrator_streams(monkeypatch) -> None:
    """When the orchestrator emits an event before the deadline, the
    watchdog must stand down."""
    sid = "s-quiet"
    event_bus.remove(sid)
    event_bus.get_or_create(sid)

    monkeypatch.setenv("HIVE_ORCH_STALL_WARN_S", "0.1")

    async def streams_immediately(**kwargs):
        # Mimic what orchestrator_node does on its first call.
        await event_bus.emit(kwargs["session_id"], {"type": "orchestrator_thinking"})
        await asyncio.sleep(0.3)
        return None

    monkeypatch.setattr("backend.api.http.run_session", streams_immediately)

    runner = asyncio.create_task(http_mod._session_runner(
        session_id=sid, task="t", model="claude:sonnet",
        approval_mode="manual", project_path="/tmp", max_turns=1,
    ))
    try:
        await asyncio.sleep(0.4)
        hints = [
            e for e in event_bus.events_since(sid, 0)
            if e.get("type") == "orchestrator_stall_hint"
        ]
        assert hints == [], "watchdog must stay silent once events flow"
    finally:
        runner.cancel()
        try:
            await runner
        except (asyncio.CancelledError, Exception):
            pass
        event_bus.remove(sid)
