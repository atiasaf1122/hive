"""Trajectory endpoint (D7) — the session's full story, ordered and typed.

    GET /api/sessions/{id}/trajectory

Read-only, built entirely from existing state: the persisted event log
plus the checkpoint conversation history. No new backend state.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.persistence.events import get_session, get_session_events
from backend.workers.base import EventType

router = APIRouter(prefix="/api/sessions")

# event type → (category, human title builder)
_CATEGORY: dict[str, str] = {
    str(EventType.AGENT_START): "lifecycle",
    str(EventType.AGENT_END): "lifecycle",
    str(EventType.AGENT_ERROR): "error",
    str(EventType.MCP_ATTACHED): "mcp",
    str(EventType.VALIDATION_FAILED): "validation",
    str(EventType.REVIEW_LLM): "review",
    str(EventType.LESSON_DISCARDED): "lesson",
    str(EventType.COMPACTION): "compaction",
    str(EventType.ESTIMATE_ACTUAL): "estimate",
    str(EventType.TOOL_USE): "tool",
    str(EventType.TOOL_RESULT): "tool",
    str(EventType.TEXT_DELTA): "text",
    str(EventType.TEXT_DONE): "text",
    str(EventType.COST): "cost",
    str(EventType.RATE_LIMIT): "error",
}


def _title(ev: dict) -> str:
    etype = ev["type"]
    payload = ev.get("payload") or {}
    raw = payload.get("raw_payload") or {}
    if etype == str(EventType.AGENT_START):
        return "agent started"
    if etype == str(EventType.AGENT_END):
        return "agent finished"
    if etype == str(EventType.AGENT_ERROR):
        origin = payload.get("origin") or "unknown"
        return f"error [{origin}]: {(payload.get('error') or '')[:120]}"
    if etype == str(EventType.MCP_ATTACHED):
        return "equipped: " + ", ".join(raw.get("servers") or [])
    if etype == str(EventType.VALIDATION_FAILED):
        return "validation FAILED: " + "; ".join(raw.get("findings") or [])[:150]
    if etype == str(EventType.REVIEW_LLM):
        return "LLM review intervention"
    if etype == str(EventType.LESSON_DISCARDED):
        return f"lesson discarded (gate {raw.get('score')}): {raw.get('title', '')}"
    if etype == str(EventType.COMPACTION):
        return f"context compacted ({raw.get('pruned_turns')} turns → state doc)"
    if etype == str(EventType.ESTIMATE_ACTUAL):
        return (f"estimate ${(raw.get('estimate') or {}).get('cost_median_usd')} "
                f"vs actual ${raw.get('actual_cost_usd')}")
    if etype == str(EventType.TOOL_USE):
        return f"tool: {payload.get('tool_name') or '?'}"
    if etype == str(EventType.COST):
        return (f"cost: {payload.get('input_tokens') or 0}→"
                f"{payload.get('output_tokens') or 0} tokens, "
                f"${payload.get('cost_usd') or 0:.4f}")
    if etype == str(EventType.TEXT_DONE):
        return (payload.get("text") or "")[:150]
    return etype


@router.get("/{session_id}/trajectory")
async def get_trajectory(session_id: str, include_deltas: bool = False) -> dict:
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    nodes: list[dict] = []
    for ev in await get_session_events(session_id):
        if not include_deltas and ev["type"] == str(EventType.TEXT_DELTA):
            continue   # per-token noise — opt-in only
        nodes.append({
            "ts": ev["ts"],
            "agent_id": ev["agent_id"],
            "category": _CATEGORY.get(ev["type"], "other"),
            "type": ev["type"],
            "title": _title(ev),
            "payload": ev["payload"],
        })

    # Conversation turns from the checkpoint (user/assistant messages don't
    # live in the events table).
    try:
        from backend.orchestrator.graph import get_conversation_history
        for m in await get_conversation_history(session_id):
            nodes.append({
                "ts": float(m.get("ts") or 0),
                "agent_id": "user" if m.get("role") == "user" else "orchestrator",
                "category": "message",
                "type": f"message/{m.get('role')}",
                "title": (m.get("content") or "")[:150],
                "payload": {"content": m.get("content")},
            })
    except Exception:  # noqa: BLE001
        pass  # deleted checkpoints / test DBs — events alone still render

    nodes.sort(key=lambda n: n["ts"])
    return {"session_id": session_id, "trajectory": nodes}
