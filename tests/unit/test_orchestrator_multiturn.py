"""Tests for the orchestrator-first multi-turn session model.

The orchestrator is the user's permanent contact:
  - Decides per-message whether to chat or spawn agents
  - Session stays alive until the user closes it
  - Conversation history persists across turns
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.orchestrator.graph import (
    SessionInterrupt,
    build_graph,
    get_conversation_history,
    orchestrator_node,
    respond_node,
    resume_session_with_value,
    run_session,
    wait_for_user_node,
)
from backend.orchestrator.nodes.planner import (
    OrchestratorDecision,
    TeamComposition,
    TeamMember,
)
from backend.persistence.db import init_db


# ── orchestrator decision unit tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_orchestrator_node_appends_user_message_to_history() -> None:
    """orchestrator_node should record the pending message before deciding."""
    decision = OrchestratorDecision(
        response="Hello!",
        composition=TeamComposition(team=[], confidence=0.9, rationale="chat"),
    )

    async def fake_orchestrate(message, session_id, history, model="claude:sonnet", project_path=None):
        return decision

    with patch("backend.orchestrator.graph.orchestrate", fake_orchestrate):
        result = await orchestrator_node({
            "session_id": "s1",
            "task": "Hi",
            "pending_message": "Hi",
            "conversation_history": [],
        })

    assert result["last_response"] == "Hello!"
    history = result["conversation_history"]
    assert len(history) == 1
    assert history[0] == {"role": "user", "content": "Hi", "ts": history[0]["ts"]}


@pytest.mark.asyncio
async def test_orchestrator_node_routes_chat_to_respond() -> None:
    """Empty team → router sends us to respond_node, not approval."""
    from backend.orchestrator.graph import _route_after_orchestrator
    state = {
        "team_composition": {
            "team": [],
            "confidence": 0.9,
            "rationale": "chat",
        }
    }
    assert _route_after_orchestrator(state) == "respond"


@pytest.mark.asyncio
async def test_orchestrator_node_routes_team_to_approval() -> None:
    """Active team → router sends us to approval."""
    from backend.orchestrator.graph import _route_after_orchestrator
    state = {
        "team_composition": {
            "team": [{"role": "Builder", "model": "claude:sonnet", "count": 1, "passive": False}],
            "confidence": 0.9,
            "rationale": "build",
        }
    }
    assert _route_after_orchestrator(state) == "approval"


@pytest.mark.asyncio
async def test_orchestrator_node_routes_passive_only_to_respond() -> None:
    """A team with only passive members has no active work → respond."""
    from backend.orchestrator.graph import _route_after_orchestrator
    state = {
        "team_composition": {
            "team": [{"role": "Debugger", "model": "claude:sonnet", "count": 1, "passive": True}],
            "confidence": 0.9,
            "rationale": "observe",
        }
    }
    assert _route_after_orchestrator(state) == "respond"


@pytest.mark.asyncio
async def test_respond_node_appends_assistant_message() -> None:
    state = {
        "session_id": "s1",
        "last_response": "Hi there!",
        "conversation_history": [{"role": "user", "content": "Hi", "ts": 0}],
    }
    result = await respond_node(state)
    history = result["conversation_history"]
    assert len(history) == 2
    assert history[1]["role"] == "assistant"
    assert history[1]["content"] == "Hi there!"
    assert result["result"]["status"] == "completed"
    assert result["result"]["text_output"] == "Hi there!"


# ── end-to-end multi-turn run ────────────────────────────────────────────────

def _patch_chat_only_orchestrator(reply: str):
    """Patch orchestrator_node to behave as a chat-only assistant (empty team)."""
    import backend.orchestrator.graph as gmod
    import time

    async def fake(state):
        message = state.get("pending_message") or state["task"]
        history = list(state.get("conversation_history") or [])
        if not history or history[-1].get("content") != message:
            history.append({"role": "user", "content": message, "ts": time.time()})
        return {
            "team_composition": {"team": [], "confidence": 0.9, "rationale": "chat"},
            "last_response": reply,
            "conversation_history": history,
        }

    return patch.object(gmod, "orchestrator_node", fake)


@pytest.mark.asyncio
async def test_session_parks_at_wait_for_user_after_first_turn(tmp_path) -> None:
    """After a chat-only first turn, session should park at awaiting_input."""
    db = tmp_path / "test.db"
    await init_db(db)

    with _patch_chat_only_orchestrator("Hi! How can I help?"):
        result = await run_session(
            session_id="sess-chat-1",
            agent_id="a1",
            task="hello",
            model="claude:sonnet",
            worktree_path=str(tmp_path),
            max_turns=5,
            db_path=db,
            approval_mode="full-auto",
        )

    assert isinstance(result, SessionInterrupt)
    assert result.payload["type"] == "awaiting_input"
    assert result.payload["last_response"] == "Hi! How can I help?"


@pytest.mark.asyncio
async def test_user_can_send_multiple_messages(tmp_path) -> None:
    """Each new user message re-enters the orchestrator and parks again."""
    db = tmp_path / "test.db"
    await init_db(db)

    with _patch_chat_only_orchestrator("first reply"):
        first = await run_session(
            session_id="sess-multi",
            agent_id="a1",
            task="message 1",
            model="claude:sonnet",
            worktree_path=str(tmp_path),
            max_turns=5,
            db_path=db,
            approval_mode="full-auto",
        )
    assert isinstance(first, SessionInterrupt)

    with _patch_chat_only_orchestrator("second reply"):
        second = await resume_session_with_value(
            session_id="sess-multi",
            resume_value={"text": "message 2"},
            db_path=db,
        )

    assert isinstance(second, SessionInterrupt)
    assert second.payload["type"] == "awaiting_input"
    assert second.payload["last_response"] == "second reply"

    history = await get_conversation_history("sess-multi", db_path=db)
    contents = [(h["role"], h["content"]) for h in history]
    assert ("user", "message 1") in contents
    assert ("assistant", "first reply") in contents
    assert ("user", "message 2") in contents
    assert ("assistant", "second reply") in contents


@pytest.mark.asyncio
async def test_closing_session_returns_final_result(tmp_path) -> None:
    """When user closes the session, the graph reaches END with the last per-turn result."""
    db = tmp_path / "test.db"
    await init_db(db)

    with _patch_chat_only_orchestrator("ok bye"):
        first = await run_session(
            session_id="sess-close",
            agent_id="a1",
            task="bye",
            model="claude:sonnet",
            worktree_path=str(tmp_path),
            max_turns=5,
            db_path=db,
            approval_mode="full-auto",
        )
    assert isinstance(first, SessionInterrupt)

    with _patch_chat_only_orchestrator("never called"):
        final = await resume_session_with_value(
            session_id="sess-close",
            resume_value={"close": True},
            db_path=db,
        )

    # final is the AgentResult of the most recent turn — the respond_node output
    assert not isinstance(final, SessionInterrupt)
    assert final["status"] == "completed"
    assert final["text_output"] == "ok bye"


@pytest.mark.asyncio
async def test_close_marks_session_closed_in_db(tmp_path) -> None:
    """Closing the session updates the DB row to status='closed'."""
    from backend.persistence.events import get_session

    db = tmp_path / "test.db"
    await init_db(db)

    with _patch_chat_only_orchestrator("hi"):
        await run_session(
            session_id="sess-status",
            agent_id="a1",
            task="hi",
            model="claude:sonnet",
            worktree_path=str(tmp_path),
            max_turns=5,
            db_path=db,
            approval_mode="full-auto",
        )

    with _patch_chat_only_orchestrator("hi"):
        await resume_session_with_value(
            session_id="sess-status",
            resume_value={"close": True},
            db_path=db,
        )

    row = await get_session("sess-status", db_path=db)
    assert row is not None
    assert row["status"] == "closed"
