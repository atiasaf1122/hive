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
    http_mod._session_to_corr_ids.clear()
    http_mod._pending_inputs.clear()
    http_mod._running_tasks.clear()
    http_mod._message_queues.clear()


def _fake_session(session_id: str, status: str = "active") -> dict:
    return {
        "id": session_id, "name": "t", "path": "/tmp", "type": "one-shot",
        "status": status, "approval_mode": "full-auto",
        "created_at": "", "last_active": "",
    }


def test_message_resolves_pending_input_future(client: TestClient) -> None:
    """If the runner has a future parked, /message resolves it directly (not queued)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        future: asyncio.Future = loop.create_future()
        http_mod._pending_inputs["s1"] = future

        with patch("backend.api.http.get_session",
                   new_callable=AsyncMock, return_value=_fake_session("s1")):
            resp = client.post("/api/sessions/s1/message", json={"text": "hello orchestrator"})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "queued": False}
        assert future.done()
        assert future.result() == {"text": "hello orchestrator", "task_shape": "auto"}
    finally:
        loop.close()


def test_message_queues_when_no_pending_future(client: TestClient) -> None:
    """If no future is parked and no runner is attached, /message queues the
    text AND auto-resumes the session so the queue is actually consumed
    (previously the message sat in an in-memory deque forever)."""
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value=_fake_session("s2")), \
         patch("backend.api.http._relaunch_for_resume",
               new_callable=AsyncMock) as relaunch:
        resp = client.post("/api/sessions/s2/message", json={"text": "queued msg"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "queued": True, "resumed": True}
    assert list(http_mod._get_queue("s2")) == [("queued msg", "auto")]
    relaunch.assert_awaited_once()


def test_message_queues_without_resume_when_runner_live(client: TestClient) -> None:
    """A live runner (agents mid-flight) means queue only — no relaunch."""
    from unittest.mock import MagicMock

    http_mod._running_tasks["s2b"] = MagicMock()
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value=_fake_session("s2b")), \
         patch("backend.api.http._relaunch_for_resume",
               new_callable=AsyncMock) as relaunch:
        resp = client.post("/api/sessions/s2b/message", json={"text": "queued msg"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "queued": True}
    relaunch.assert_not_awaited()


def test_message_unknown_session_404(client: TestClient) -> None:
    """Messaging a nonexistent session must 404 instead of silently queueing."""
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value=None):
        resp = client.post("/api/sessions/nope/message", json={"text": "hi"})
    assert resp.status_code == 404


# ── /resume (re-attach a runner after restart) ─────────────────────────────


def test_resume_idle_session_relaunches(client: TestClient) -> None:
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value=_fake_session("r1", status="idle")), \
         patch("backend.api.http._relaunch_for_resume",
               new_callable=AsyncMock) as relaunch:
        resp = client.post("/api/sessions/r1/resume")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "resuming"}
    relaunch.assert_awaited_once()


def test_resume_unknown_session_404(client: TestClient) -> None:
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value=None):
        resp = client.post("/api/sessions/ghost/resume")
    assert resp.status_code == 404


def test_resume_closed_session_409(client: TestClient) -> None:
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value=_fake_session("r2", status="closed")):
        resp = client.post("/api/sessions/r2/resume")
    assert resp.status_code == 409


def test_resume_noop_when_runner_live(client: TestClient) -> None:
    from unittest.mock import MagicMock

    http_mod._running_tasks["r3"] = MagicMock()
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value=_fake_session("r3")), \
         patch("backend.api.http._relaunch_for_resume",
               new_callable=AsyncMock) as relaunch:
        resp = client.post("/api/sessions/r3/resume")
    assert resp.status_code == 200
    assert resp.json()["status"] == "already-running"
    relaunch.assert_not_awaited()


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


# ── Approval correlation IDs (invariant #5) ─────────────────────────────


def _set_resolve_no_op() -> None:
    """resolve_pending_approval reads/writes SQLite; patch out for the
    pure-in-memory approve_session tests below."""


def test_approve_with_correlation_id_resolves_waiter(client: TestClient) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        future: asyncio.Future = loop.create_future()
        http_mod._register_approval("corr-a", "s1", future)

        with patch("backend.api.http.resolve_pending_approval",
                   new_callable=AsyncMock, return_value=True):
            resp = client.post(
                "/api/sessions/s1/approve",
                json={"approved": True, "correlation_id": "corr-a"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["correlation_id"] == "corr-a"
        assert future.done()
        assert future.result() == {"approved": True}
        assert "corr-a" not in http_mod._pending_approvals
    finally:
        loop.close()


def test_approve_without_correlation_id_falls_back_when_single(client: TestClient) -> None:
    """Legacy client compatibility: if exactly one approval is pending for
    the session, the backend infers the correlation_id."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        future: asyncio.Future = loop.create_future()
        http_mod._register_approval("only-one", "s1", future)

        with patch("backend.api.http.resolve_pending_approval",
                   new_callable=AsyncMock, return_value=True):
            resp = client.post("/api/sessions/s1/approve", json={"approved": False})

        assert resp.status_code == 200
        assert resp.json()["correlation_id"] == "only-one"
        assert future.done()
    finally:
        loop.close()


def test_approve_without_correlation_id_refuses_when_ambiguous(client: TestClient) -> None:
    """Two parallel approvals for one session — legacy clients can't pick
    one safely, so the backend must refuse rather than guess."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        f1 = loop.create_future()
        f2 = loop.create_future()
        http_mod._register_approval("corr-1", "s1", f1)
        http_mod._register_approval("corr-2", "s1", f2)

        resp = client.post("/api/sessions/s1/approve", json={"approved": True})
        assert resp.status_code == 400
        assert "correlation_id" in resp.json()["detail"]
        assert not f1.done()
        assert not f2.done()
    finally:
        loop.close()


def test_approve_unknown_correlation_id_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/sessions/whatever/approve",
        json={"approved": True, "correlation_id": "ghost"},
    )
    assert resp.status_code == 404


def test_parallel_approvals_resolve_independently(client: TestClient) -> None:
    """Two concurrent team_approval interrupts for one session must each
    have their own correlation_id and resolve independently."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        f1: asyncio.Future = loop.create_future()
        f2: asyncio.Future = loop.create_future()
        http_mod._register_approval("ca", "sX", f1)
        http_mod._register_approval("cb", "sX", f2)

        with patch("backend.api.http.resolve_pending_approval",
                   new_callable=AsyncMock, return_value=True):
            r1 = client.post(
                "/api/sessions/sX/approve",
                json={"approved": True, "correlation_id": "ca"},
            )
            assert r1.status_code == 200
            assert f1.done() and f1.result() == {"approved": True}
            assert not f2.done(), "second approval must remain in flight"

            r2 = client.post(
                "/api/sessions/sX/approve",
                json={"approved": False, "correlation_id": "cb"},
            )
            assert r2.status_code == 200
            assert f2.done() and f2.result() == {"approved": False}
    finally:
        loop.close()


def test_unregister_clears_session_index(client: TestClient) -> None:
    """_unregister_approval cleans up both maps so no stale state leaks."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        f1 = loop.create_future()
        http_mod._register_approval("only", "sY", f1)
        assert http_mod._session_corr_ids("sY") == ["only"]

        http_mod._unregister_approval("only", "sY")
        assert "only" not in http_mod._pending_approvals
        assert http_mod._session_corr_ids("sY") == []
        assert "sY" not in http_mod._session_to_corr_ids
    finally:
        loop.close()


# ── /cancel never crashes the parked-at-input session ─────────────────────


def test_cancel_resolves_pending_input_with_close_not_cancel(client: TestClient) -> None:
    """When the runner is parked at awaiting_input, /cancel must resolve
    the pending input future cleanly (close=True) rather than future.cancel()
    — which would raise CancelledError out of the await and force the runner
    into its `except Exception` branch, emitting session_error instead of
    session_cancelled."""
    from unittest.mock import MagicMock

    # Stand-ins for the loop-bound objects. cancel_session reads the
    # future via _pending_inputs.get() and the task via _running_tasks.get();
    # we replace both with simple mocks that record interactions.
    pi = MagicMock()
    pi.done.return_value = False
    captured: dict = {}
    pi.set_result.side_effect = lambda v: captured.setdefault("resume_value", v)
    http_mod._pending_inputs["scan"] = pi

    task = MagicMock()
    task.cancel = MagicMock()
    http_mod._running_tasks["scan"] = task

    async def _instant_wait(_aw, timeout=None):  # noqa: ARG001
        return None

    with patch("backend.api.http.update_session_status",
               new_callable=AsyncMock, return_value=None), \
         patch("backend.api.http.asyncio.wait_for", new=_instant_wait):
        resp = client.post("/api/sessions/scan/cancel")

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    # Critical: set_result was called (clean close) — pi.cancel must NOT
    # have been called, otherwise the runner crashes with CancelledError.
    pi.set_result.assert_called_once()
    pi.cancel.assert_not_called()
    assert captured["resume_value"] == {"close": True}
    # task.cancel must NOT have been called either when we took the
    # parked path — the runner is unwinding on its own.
    task.cancel.assert_not_called()


# ── DELETE /sessions/{id} (hard delete) ────────────────────────────────────


def test_delete_session_endpoint(client: TestClient) -> None:
    wt = AsyncMock()
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value=_fake_session("d1")), \
         patch("backend.api.http.delete_session_data",
               new_callable=AsyncMock, return_value=True) as deleter, \
         patch("backend.worktrees.manager.WorktreeManager") as manager_cls:
        manager_cls.return_value.remove_session_worktrees = wt
        resp = client.delete("/api/sessions/d1")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "deleted"}
    deleter.assert_awaited_once_with("d1")
    wt.assert_awaited_once()


def test_delete_session_404_on_missing(client: TestClient) -> None:
    with patch("backend.api.http.get_session",
               new_callable=AsyncMock, return_value=None):
        resp = client.delete("/api/sessions/ghost")
    assert resp.status_code == 404
