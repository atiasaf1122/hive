"""D3 — orchestrator compaction: threshold trigger, state-doc turns, no loss."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.orchestrator.compaction import (
    COMPACT_EVERY_TURNS,
    KEEP_LAST_TURNS,
    estimate_tokens,
    should_compact,
)
from backend.orchestrator.graph import orchestrator_node
from backend.orchestrator.nodes.planner import (
    OrchestratorDecision,
    TeamComposition,
    _build_prompt,
)
from backend.workers.base import EventType


def _turn(i: int, size: int = 40) -> dict:
    return {"role": "user" if i % 2 == 0 else "assistant",
            "content": f"turn {i} " + "x" * size, "ts": float(i)}


def test_should_compact_thresholds() -> None:
    small = [_turn(i) for i in range(4)]
    assert not should_compact(small, turns_since_compaction=1)
    assert should_compact(small, turns_since_compaction=COMPACT_EVERY_TURNS)
    huge = [_turn(i, size=30_000) for i in range(4)]
    assert estimate_tokens(huge) > 20_000
    assert should_compact(huge, turns_since_compaction=1)


def test_state_doc_lands_in_planner_prompt() -> None:
    prompt = _build_prompt("next task", [], state_doc="# Goal\nShip the API")
    assert "Current project state (compacted from earlier turns):" in prompt
    assert "Ship the API" in prompt
    assert "Current project state" not in _build_prompt("next task", [])


@pytest.mark.asyncio
async def test_compaction_prunes_history_and_persists_event() -> None:
    history = [_turn(i, size=30_000) for i in range(8)]   # way past threshold
    state = {
        "session_id": "s-c", "task": "build the thing",
        "pending_message": "continue",
        "conversation_history": history,
        "state_doc": "", "turns_since_compaction": 1,
        "project_path": "/tmp",
    }
    chat = OrchestratorDecision(
        response="ok", composition=TeamComposition(team=[], confidence=0.9, rationale=""))
    written = []

    async def fake_write(event, path=None):
        written.append(event)

    with patch("backend.orchestrator.graph.orchestrate",
               new_callable=AsyncMock, return_value=chat) as orch, \
         patch("backend.orchestrator.compaction.build_state_doc",
               new_callable=AsyncMock, return_value="# Goal\ncompact doc") as builder, \
         patch("backend.orchestrator.graph.write_event", side_effect=fake_write), \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        out = await orchestrator_node(state)  # type: ignore[arg-type]

    builder.assert_awaited_once()
    # History pruned to the last K turns (+ the new pending message).
    assert len(out["conversation_history"]) <= KEEP_LAST_TURNS + 1
    assert out["state_doc"] == "# Goal\ncompact doc"
    assert out["turns_since_compaction"] == 0
    # Orchestrate received the state doc.
    assert orch.await_args.kwargs["state_doc"] == "# Goal\ncompact doc"
    # Nothing lost: pruned turns are inside the compaction event.
    comp_events = [e for e in written if str(e.type) == str(EventType.COMPACTION)]
    assert len(comp_events) == 1
    assert comp_events[0].raw_payload["pruned_turns"] >= 4
    assert comp_events[0].raw_payload["pruned"][0]["content"].startswith("turn 0")


@pytest.mark.asyncio
async def test_no_compaction_under_threshold() -> None:
    state = {
        "session_id": "s-c2", "task": "t", "pending_message": "hi",
        "conversation_history": [_turn(0), _turn(1)],
        "state_doc": "", "turns_since_compaction": 1,
        "project_path": "/tmp",
    }
    chat = OrchestratorDecision(
        response="ok", composition=TeamComposition(team=[], confidence=0.9, rationale=""))
    with patch("backend.orchestrator.graph.orchestrate",
               new_callable=AsyncMock, return_value=chat), \
         patch("backend.orchestrator.compaction.build_state_doc",
               new_callable=AsyncMock) as builder, \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        out = await orchestrator_node(state)  # type: ignore[arg-type]

    builder.assert_not_awaited()
    assert out["turns_since_compaction"] == 2
    assert out["state_doc"] == ""


@pytest.mark.asyncio
async def test_compaction_failure_keeps_full_history() -> None:
    history = [_turn(i, size=30_000) for i in range(8)]
    state = {
        "session_id": "s-c3", "task": "t", "pending_message": "go",
        "conversation_history": history,
        "state_doc": "", "turns_since_compaction": 1,
        "project_path": "/tmp",
    }
    chat = OrchestratorDecision(
        response="ok", composition=TeamComposition(team=[], confidence=0.9, rationale=""))
    with patch("backend.orchestrator.graph.orchestrate",
               new_callable=AsyncMock, return_value=chat), \
         patch("backend.orchestrator.compaction.build_state_doc",
               new_callable=AsyncMock, return_value=None), \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        out = await orchestrator_node(state)  # type: ignore[arg-type]

    # Doc build failed → nothing pruned, nothing lost.
    assert len(out["conversation_history"]) >= 8
    assert out["state_doc"] == ""
