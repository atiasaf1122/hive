"""Per-session asyncio event queues for WebSocket streaming."""
from __future__ import annotations

import asyncio
from typing import Any

_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}


def get_or_create(session_id: str) -> asyncio.Queue[dict[str, Any]]:
    if session_id not in _queues:
        _queues[session_id] = asyncio.Queue(maxsize=2000)
    return _queues[session_id]


def remove(session_id: str) -> None:
    _queues.pop(session_id, None)


async def emit(session_id: str, payload: dict[str, Any]) -> None:
    q = get_or_create(session_id)
    try:
        q.put_nowait(payload)
    except asyncio.QueueFull:
        pass  # drop — client must handle gaps
