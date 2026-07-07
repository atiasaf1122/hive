"""Part-3 regressions (session 49641e2b, post-1.0).

a) MISROUTE — "give me a small prompt..." (deliverable = text to copy from
   the chat) was classified SOLO Writer and produced an unrequested file.
   The rubric must steer capability questions and text-deliverable requests
   to CHAT; guarded here by content assertions so the guidance can't be
   silently dropped.
b) STUCK SPINNER — after a solo turn completes, the UI busy state clears on
   orchestrator_response / awaiting_user. Assert both events are emitted by
   the solo completion path.
c) WRAP-UP OVERCLAIM — the solo wrap-up repeated the "On it — routing this
   as a single Writer task" status line before the deliverable, narrating it
   as if it answered the user's message. Solo wrap-ups now frame the output
   as produced work.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.orchestrator.task_router import _RUBRIC


# ── (a) rubric guards ─────────────────────────────────────────────────────────

def test_rubric_routes_capability_questions_to_chat() -> None:
    assert "what can I do with hive?" in _RUBRIC
    assert "capabilities" in _RUBRIC


def test_rubric_routes_text_deliverables_to_chat() -> None:
    """A prompt/advice/plan the user will copy from the chat is CHAT even
    when phrased imperatively — the 49641e2b misroute."""
    assert "give me a prompt" in _RUBRIC
    assert "even when phrased imperatively" in _RUBRIC
    # solo requires an explicit on-disk artifact
    assert "explicitly wants" in _RUBRIC


# ── shared solo-state fixture ────────────────────────────────────────────────

def _solo_review_state() -> dict:
    agent = {
        "agent_id": "writer-1", "role": "Writer", "model": "ollama:qwen3-coder:30b",
        "worktree_path": "/tmp/wt", "subtask": "write the prompt", "branch": "b1",
    }
    return {
        "session_id": "sess-solo-wrap",
        "spawn_plan": {
            "session_id": "sess-solo-wrap", "project_path": "/tmp",
            "active_agents": [agent], "passive_agents": [],
        },
        "worker_results": {
            "writer-1": {
                "agent_id": "writer-1", "status": "completed",
                "text_output": "wrote prompt_for_hive_full_potential.md",
                "summary": "[wrote prompt_for_hive_full_potential.md]",
                "input_tokens": 10, "output_tokens": 20, "cost_usd": 0.0,
                "error": None,
            },
        },
        "conversation_history": [],
        "last_response": "On it — routing this as a single Writer task (crafting).",
        "team_composition": {"team": [], "solo": True},
        "db_path": "/tmp/x.db",
    }


def _clean_report() -> SimpleNamespace:
    return SimpleNamespace(
        conflicts=[], failed_agents=[], merged=["writer-1"], notes=[],
        success=True, total_commits_merged=1,
    )


# ── (b) + (c) solo completion path ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_solo_wrapup_does_not_narrate_deliverable_as_answer() -> None:
    from backend.orchestrator import graph as gmod

    emitted: list[dict] = []

    async def capture_ws(session_id: str, payload: dict) -> None:
        emitted.append(payload)

    with patch.object(gmod, "review_and_merge",
                      new=AsyncMock(return_value=_clean_report())), \
         patch.object(gmod, "write_event", new=AsyncMock()), \
         patch.object(gmod, "_emit_to_ws", new=capture_ws):
        out = await gmod.review_node(_solo_review_state())

    final = out["conversation_history"][-1]["content"]
    # (c) the routing status line must not be re-narrated in the wrap-up
    assert "On it — routing" not in final
    assert "Here's what the agent produced" in final
    assert "[writer-1]" in final
    # (b) the busy-clearing response event fired with the same final text
    responses = [e for e in emitted if e["type"] == "orchestrator_response"]
    assert len(responses) == 1 and responses[0]["text"] == final


@pytest.mark.asyncio
async def test_swarm_wrapup_keeps_planner_response() -> None:
    """Non-solo turns keep last_response (the planner's meaningful reply)."""
    from backend.orchestrator import graph as gmod

    state = _solo_review_state()
    state["team_composition"] = {"team": []}  # no solo marker
    state["last_response"] = "I split this into two workers."

    with patch.object(gmod, "review_and_merge",
                      new=AsyncMock(return_value=_clean_report())), \
         patch.object(gmod, "write_event", new=AsyncMock()), \
         patch.object(gmod, "_emit_to_ws", new=AsyncMock()):
        out = await gmod.review_node(state)

    final = out["conversation_history"][-1]["content"]
    assert final.startswith("I split this into two workers.")
    assert "[writer-1]" in final


@pytest.mark.asyncio
async def test_wait_for_user_emits_awaiting_user_which_clears_ui_busy() -> None:
    """The frontend clears its planner spinner on awaiting_user — the solo
    path must always reach this emit after review."""
    from backend.orchestrator import graph as gmod

    emitted: list[dict] = []

    async def capture_ws(session_id: str, payload: dict) -> None:
        emitted.append(payload)

    with patch.object(gmod, "_emit_to_ws", new=capture_ws), \
         patch.object(gmod, "interrupt",
                      return_value={"message": "next turn", "task_shape": "auto"}):
        await gmod.wait_for_user_node({
            "session_id": "sess-solo-wrap",
            "user_closed": False,
            "last_response": "final text",
        })

    kinds = [e["type"] for e in emitted]
    assert "awaiting_user" in kinds
    aw = next(e for e in emitted if e["type"] == "awaiting_user")
    assert aw["last_response"] == "final text"
