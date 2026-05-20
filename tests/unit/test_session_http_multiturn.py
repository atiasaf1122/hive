"""HTTP-level tests for the orchestrator-first multi-turn session API.

Covers:
  - POST /api/sessions/{id}/message resolves a pending input future
  - POST /api/sessions/{id}/message queues if no pending future
  - POST /api/sessions/{id}/close resolves a pending input future with close=True
  - POST /api/sessions/{id}/close on an unknown session → 404
  - GET /api/sessions/{id}/history → conversation history
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.api import http as http_mod


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    # Reset module-level state between tests
    http_mod._pending_approvals.clear()
    http_mod._pending_inputs.clear()
    http_mod._running_tasks.clear()
    http_mod._message_queues.clear()


def test_message_resolves_pending_input_future(client: TestClient) -> None:
    """If the runner has a future parked, /message resolves it directly (not queued)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        future: asyncio.Future = loop.create_future()
        http_mod._pending_inputs["s1"] = future

        resp = client.post("/api/sessions/s1/message", json={"text": "hello orchestrator"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "queued": False}
        assert future.done()
        assert future.result() == {"text": "hello orchestrator"}
    finally:
        loop.close()


def test_message_queues_when_no_pending_future(client: TestClient) -> None:
    """If no future is parked (e.g. agents running), /message queues the text."""
    resp = client.post("/api/sessions/s2/message", json={"text": "queued msg"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "queued": True}
    assert list(http_mod._get_queue("s2")) == ["queued msg"]


def test_message_empty_text_rejected(client: TestClient) -> None:
    resp = client.post("/api/sessions/s3/message", json={"text": "   "})
    assert resp.status_code == 400


def test_close_resolves_pending_input_with_close_true(client: TestClient) -> None:
    """When parked at wait_for_user, /close resolves the future with {"close": True}."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        future: asyncio.Future = loop.create_future()
        http_mod._pending_inputs["s4"] = future

        with patch("backend.api.http.get_session",
                   new_callable=AsyncMock, return_value={"id": "s4", "status": "active"}):
            resp = client.post("/api/sessions/s4/close")

        assert resp.status_code == 200
        assert resp.json()["status"] == "closing"
        assert future.done()
        assert future.result() == {"close": True}
    finally:
        loop.close()


def test_close_marks_status_when_not_parked(client: TestClient) -> None:
    """If session is busy (no parked future), /close marks status closed directly."""
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value={"id": "s5", "status": "active"}), \
         patch("backend.api.http.update_session_status", new_callable=AsyncMock) as upd:
        resp = client.post("/api/sessions/s5/close")

    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    upd.assert_awaited_once_with("s5", "closed")


def test_close_unknown_session_404(client: TestClient) -> None:
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value=None):
        resp = client.post("/api/sessions/missing/close")
    assert resp.status_code == 404


def test_history_endpoint(client: TestClient) -> None:
    fake_history = [
        {"role": "user", "content": "hi", "ts": 0},
        {"role": "assistant", "content": "hello", "ts": 1},
    ]
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value={"id": "s6", "status": "active"}), \
         patch("backend.api.http.get_conversation_history",
               new_callable=AsyncMock, return_value=fake_history):
        resp = client.get("/api/sessions/s6/history")

    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s6"
    assert body["history"] == fake_history


def test_history_unknown_session_404(client: TestClient) -> None:
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value=None):
        resp = client.get("/api/sessions/missing/history")
    assert resp.status_code == 404
