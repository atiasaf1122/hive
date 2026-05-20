"""Per-session asyncio event queues for WebSocket streaming.

Phase 10 hardening (Section 8.2): in addition to the live queue, we
keep a per-session ring buffer of the most recent events tagged with
monotonic IDs. When a client reconnects after a network blip, it sends
its last seen `event_id` and we replay everything since.

The ring is capped at `MAX_REPLAY` (1 000 events per session) — that's
~5 KB per event × 1 000 = ~5 MB worst case per active session, which
is fine for the single-user desktop model. The cap is the price of
not wiring this through the SQLite `events` table.
"""
from __future__ import annotations

import asyncio
from collections import deque
from itertools import count
from typing import Any, Deque

_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
_rings: dict[str, Deque[dict[str, Any]]] = {}
_id_counter = count(1)

MAX_REPLAY = 1_000


def get_or_create(session_id: str) -> asyncio.Queue[dict[str, Any]]:
    if session_id not in _queues:
        _queues[session_id] = asyncio.Queue(maxsize=2000)
        _rings[session_id] = deque(maxlen=MAX_REPLAY)
    return _queues[session_id]


def remove(session_id: str) -> None:
    _queues.pop(session_id, None)
    _rings.pop(session_id, None)


async def emit(session_id: str, payload: dict[str, Any]) -> None:
    """Push an event onto the live queue AND the catch-up ring.

    Adds an `event_id` (process-wide monotonic) to the payload so the
    client can later resume from where it left off. Existing
    `event_id` (if the caller already set one) is preserved.
    """
    q = get_or_create(session_id)
    if "event_id" not in payload:
        payload = {**payload, "event_id": next(_id_counter)}
    _rings[session_id].append(payload)
    try:
        q.put_nowait(payload)
    except asyncio.QueueFull:
        pass  # drop from live queue — client can recover via the ring


def events_since(session_id: str, after_id: int) -> list[dict[str, Any]]:
    """Return every retained event for the session with id > after_id."""
    ring = _rings.get(session_id)
    if not ring:
        return []
    return [e for e in list(ring) if int(e.get("event_id", 0)) > after_id]


def latest_event_id(session_id: str) -> int:
    ring = _rings.get(session_id)
    if not ring:
        return 0
    return int(ring[-1].get("event_id", 0)) if len(ring) else 0
