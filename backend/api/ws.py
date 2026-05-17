"""WebSocket endpoint — streams per-session events to the browser."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.api import event_bus

logger = logging.getLogger(__name__)
router = APIRouter()

KEEPALIVE_INTERVAL = 20.0


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    queue = event_bus.get_or_create(session_id)
    logger.info("WebSocket connected: %s", session_id)
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_INTERVAL)
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
