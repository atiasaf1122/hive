"""Regression tests for the chat-duplication bug (post-1.0, Part 1).

Root cause: `resume_from: 0` — what the frontend sends on every fresh
ProjectView mount — made the WS endpoint replay the ENTIRE event ring, so
each prior turn's orchestrator_response was re-appended on top of the
/history snapshot the UI had just fetched. Stale queue backlog (events
emitted while no client was attached) was delivered on top of that.

The fix (backend/api/ws.py): resume_from 0/absent = fresh client → no
replay, skip backlog, live-only from connection. resume_from > 0 and
within the current ring = true reconnect → replay exactly the gap, once.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.api import event_bus, ws as ws_module
from backend.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    # Mid-connection emits come from the test thread; the app loop only
    # notices them on its next wakeup. A short keepalive keeps the tests
    # fast instead of stalling up to the 20s production interval.
    monkeypatch.setattr(ws_module, "KEEPALIVE_INTERVAL", 0.05)
    with TestClient(app) as c:
        yield c


def _emit_sync(session_id: str, payload: dict, event_id: int) -> dict:
    """Synchronous replica of event_bus.emit for use under TestClient."""
    q = event_bus.get_or_create(session_id)
    stamped = {**payload, "event_id": event_id}
    event_bus._rings[session_id].append(stamped)
    q.put_nowait(stamped)
    return stamped


def _drain(ws) -> list[dict]:
    """Receive frames until session_end, returning all frames (pings incl.)."""
    frames = []
    while True:
        msg = ws.receive_json()
        frames.append(msg)
        if msg["type"] in ("session_end", "session_error"):
            return frames


def _handshake(ws, resume_from: int) -> None:
    """Send the resume frame and give the server a beat to process it —
    events emitted before the floor is computed would classify as backlog."""
    ws.send_json({"resume_from": resume_from})
    time.sleep(0.5)


def test_fresh_client_does_not_receive_prior_turns(client: TestClient):
    """Turn N's stream must not contain turn N-1's reply (the live bug).

    A fresh mount (resume_from: 0) already has /history — replaying the
    ring or draining stale backlog duplicates the whole conversation.
    """
    sid = "ws-dup-fresh"
    event_bus.remove(sid)
    _emit_sync(sid, {"type": "orchestrator_response", "text": "ALPHA"}, 101)
    _emit_sync(sid, {"type": "orchestrator_response", "text": "BRAVO"}, 102)

    with client.websocket_connect(f"/ws/{sid}") as ws:
        _handshake(ws, 0)  # what the frontend sends on mount
        # New live turn arrives after the client attached.
        _emit_sync(sid, {"type": "orchestrator_response", "text": "CHARLIE"}, 103)
        _emit_sync(sid, {"type": "session_end", "status": "completed"}, 104)
        frames = _drain(ws)

    responses = [f["text"] for f in frames if f["type"] == "orchestrator_response"]
    assert responses == ["CHARLIE"]  # not ALPHA/BRAVO — neither replayed nor backlog
    assert "ALPHA" not in " ".join(responses)
    assert "BRAVO" not in " ".join(responses)
    event_bus.remove(sid)


def test_reconnect_replays_exactly_the_gap_once(client: TestClient):
    """A true reconnect (resume_from > 0) gets the missed events once —
    not twice (ring replay + still-queued copy)."""
    sid = "ws-dup-reconnect"
    event_bus.remove(sid)
    _emit_sync(sid, {"type": "orchestrator_response", "text": "SEEN"}, 201)
    _emit_sync(sid, {"type": "orchestrator_response", "text": "MISSED"}, 202)

    with client.websocket_connect(f"/ws/{sid}") as ws:
        _handshake(ws, 201)  # saw SEEN, missed MISSED
        _emit_sync(sid, {"type": "session_end", "status": "completed"}, 203)
        frames = _drain(ws)

    responses = [f["text"] for f in frames if f["type"] == "orchestrator_response"]
    assert responses == ["MISSED"]  # gap replayed once; queued copy skipped
    event_bus.remove(sid)


def test_stale_resume_id_from_previous_process_is_treated_as_fresh(client: TestClient):
    """resume_from beyond the current ring (backend restarted) must not
    wedge the stream — live events still flow."""
    sid = "ws-dup-stale"
    event_bus.remove(sid)
    _emit_sync(sid, {"type": "orchestrator_response", "text": "OLD"}, 5)

    with client.websocket_connect(f"/ws/{sid}") as ws:
        _handshake(ws, 999_999)
        _emit_sync(sid, {"type": "orchestrator_response", "text": "NEW"}, 6)
        _emit_sync(sid, {"type": "session_end", "status": "completed"}, 7)
        frames = _drain(ws)

    responses = [f["text"] for f in frames if f["type"] == "orchestrator_response"]
    assert responses == ["NEW"]
    event_bus.remove(sid)
