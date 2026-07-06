"""WebSocket endpoint — streams per-session events to the browser.

Phase 10 hardening (Section 8.2): the first frame the client sends
(within `RESUME_WINDOW_SECONDS`) is an optional `{"resume_from": N}`
message. If present and > 0, we replay every retained event with
id > N before joining the live queue.

Post-1.0 fix (chat-duplication bug): `resume_from: 0` (or no handshake)
means a FRESH client — one that just fetched /history and holds the
authoritative snapshot. Fresh clients get NO replay and skip any stale
queue backlog; they stream live from the moment of connection. The old
behaviour (0 → replay the whole ring) re-delivered every prior
orchestrator_response on each ProjectView mount, so the UI appended the
entire conversation again on top of the fetched history.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api import event_bus

logger = logging.getLogger(__name__)
router = APIRouter()

KEEPALIVE_INTERVAL = 20.0
RESUME_WINDOW_SECONDS = 1.5


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    queue = event_bus.get_or_create(session_id)
    logger.info("WebSocket connected: %s", session_id)

    # Optional resume handshake — listen briefly for {"resume_from": N}.
    resume_after = await _maybe_read_resume(websocket)
    latest = event_bus.latest_event_id(session_id)
    if resume_after is not None and 0 < resume_after <= latest:
        # True reconnect: the client saw events from THIS backend process.
        # Replay exactly the gap.
        floor = resume_after
        missed = event_bus.events_since(session_id, floor)
        if missed:
            logger.info(
                "WebSocket %s replaying %d events since #%d",
                session_id, len(missed), floor,
            )
            for ev in missed:
                try:
                    await websocket.send_json(ev)
                except Exception:
                    return
            floor = int(missed[-1].get("event_id", floor))
    else:
        # Fresh client (resume_from 0/absent) or a resume id from a previous
        # backend process (> latest): /history is its snapshot — live-only.
        floor = latest

    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_INTERVAL)
                # Skip stale backlog: events queued while no client was
                # attached are already covered by /history or the replay.
                eid = int(payload.get("event_id", 0))
                if eid and eid <= floor:
                    queue.task_done()
                    continue
                if eid:
                    floor = eid
                await websocket.send_json(payload)
                queue.task_done()
                if payload.get("type") in ("session_end", "session_error"):
                    break
            except asyncio.TimeoutError:
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: %s", session_id)
    except Exception as exc:
        logger.warning("WebSocket error for %s: %s", session_id, exc)


async def _maybe_read_resume(websocket: WebSocket) -> int | None:
    """Wait briefly for a `{resume_from: N}` first frame from the client.

    Returns the integer event_id to replay from, or None on timeout /
    malformed input. Either way we proceed to the live stream — the
    handshake is strictly opt-in.
    """
    try:
        text = await asyncio.wait_for(
            websocket.receive_text(),
            timeout=RESUME_WINDOW_SECONDS,
        )
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None

    try:
        data = _json.loads(text)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    raw = data.get("resume_from")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
