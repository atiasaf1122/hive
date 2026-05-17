"""Phase 5 tests: event bus, REST API, WebSocket endpoint."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.api.event_bus import emit, get_or_create, remove
from backend.main import app


# ── event_bus ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_bus_get_or_create():
    remove("test-sess-1")
    q = get_or_create("test-sess-1")
    assert q is not None
    assert q is get_or_create("test-sess-1")  # same queue returned
    remove("test-sess-1")


@pytest.mark.asyncio
async def test_event_bus_emit_and_receive():
    remove("test-sess-2")
    await emit("test-sess-2", {"type": "ping", "x": 1})
    q = get_or_create("test-sess-2")
    item = q.get_nowait()
    assert item["type"] == "ping"
    assert item["x"] == 1
    remove("test-sess-2")


@pytest.mark.asyncio
async def test_event_bus_full_queue_drops_silently():
    remove("test-sess-3")
    q = get_or_create("test-sess-3")
    # Fill to maxsize
    for i in range(q.maxsize):
        await emit("test-sess-3", {"i": i})
    # This should not raise
    await emit("test-sess-3", {"type": "overflow"})
    assert q.qsize() == q.maxsize
    remove("test-sess-3")


@pytest.mark.asyncio
async def test_event_bus_remove_clears_queue():
    remove("test-sess-4")
    get_or_create("test-sess-4")
    remove("test-sess-4")
    # After remove, a new call creates a fresh queue
    q = get_or_create("test-sess-4")
    assert q.empty()
    remove("test-sess-4")


# ── REST API ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_list_sessions_empty(client: TestClient):
    with patch("backend.api.http.list_sessions", new_callable=AsyncMock, return_value=[]):
        resp = client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_sessions_returns_data(client: TestClient):
    mock_sessions = [
        {
            "id": "abc12345",
            "name": "Test task",
            "status": "completed",
            "approval_mode": "full-auto",
            "created_at": "2026-05-15T10:00:00",
            "last_active": "2026-05-15T10:05:00",
        }
    ]
    with patch("backend.api.http.list_sessions", new_callable=AsyncMock, return_value=mock_sessions):
        resp = client.get("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["session_id"] == "abc12345"
    assert data[0]["name"] == "Test task"


def test_get_session_not_found(client: TestClient):
    with patch("backend.api.http.get_session", new_callable=AsyncMock, return_value=None):
        resp = client.get("/api/sessions/nonexistent")
    assert resp.status_code == 404


def test_get_session_found(client: TestClient):
    mock_session = {
        "id": "abc12345",
        "name": "Test task",
        "status": "running",
        "approval_mode": "checkpoint",
        "created_at": "2026-05-15T10:00:00",
        "last_active": "2026-05-15T10:02:00",
    }
    with patch("backend.api.http.get_session", new_callable=AsyncMock, return_value=mock_session):
        resp = client.get("/api/sessions/abc12345")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "abc12345"
    assert data["approval_mode"] == "checkpoint"


def test_create_session_starts_background_task(client: TestClient):
    with patch("backend.api.http.run_session", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = {
            "agent_id": "worker-x", "status": "completed",
            "text_output": "done", "input_tokens": 10, "output_tokens": 20,
            "cost_usd": 0.001, "error": None,
        }
        resp = client.post("/api/sessions", json={
            "task": "Hello world",
            "model": "claude:sonnet",
            "approval_mode": "full-auto",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert len(data["session_id"]) == 8
    assert data["status"] == "starting"


def test_approve_session_no_pending(client: TestClient):
    resp = client.post("/api/sessions/nosession/approve", json={"approved": True})
    assert resp.status_code == 404


def test_approve_session_resolves_future(client: TestClient):
    from backend.api.http import _pending_approvals

    # Manually plant a future to simulate a waiting session
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    _pending_approvals["test-approval-sess"] = future

    try:
        resp = client.post("/api/sessions/test-approval-sess/approve", json={"approved": True})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # Future should be resolved
        assert future.done()
        assert future.result() == {"approved": True}
    finally:
        _pending_approvals.pop("test-approval-sess", None)
        loop.close()


def test_send_message_emits_to_queue(client: TestClient):
    remove("msg-test-sess")
    resp = client.post("/api/sessions/msg-test-sess/message", json={
        "text": "Hello agent",
        "agent_id": "builder-1",
        "urgency": "question",
    })
    assert resp.status_code == 200
    q = get_or_create("msg-test-sess")
    item = q.get_nowait()
    assert item["type"] == "user_message"
    assert item["text"] == "Hello agent"
    assert item["agent_id"] == "builder-1"
    remove("msg-test-sess")


# ── WebSocket ─────────────────────────────────────────────────────────────────

def test_websocket_receives_queued_event(client: TestClient):
    """WebSocket should deliver events that are pre-queued."""
    remove("ws-test-sess")
    # Pre-queue an event and a terminal event
    q = get_or_create("ws-test-sess")
    q.put_nowait({"type": "plan_complete", "session_id": "ws-test-sess"})
    q.put_nowait({"type": "session_end", "session_id": "ws-test-sess", "status": "completed"})

    with client.websocket_connect("/ws/ws-test-sess") as ws:
        msg1 = ws.receive_json()
        assert msg1["type"] == "plan_complete"
        msg2 = ws.receive_json()
        assert msg2["type"] == "session_end"

    remove("ws-test-sess")


def test_websocket_accepts_connection(client: TestClient):
    """WebSocket endpoint should accept connections without error."""
    remove("ws-accept-test")
    # Queue a terminal event so the WS loop exits cleanly
    q = get_or_create("ws-accept-test")
    q.put_nowait({"type": "session_end", "session_id": "ws-accept-test", "status": "completed"})

    with client.websocket_connect("/ws/ws-accept-test") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "session_end"

    remove("ws-accept-test")
