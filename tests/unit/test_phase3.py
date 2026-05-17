"""Phase 3 tests: approval modes, interrupt/resume, confidence escalation."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from backend.orchestrator.graph import (
    SessionInterrupt,
    approval_node,
    build_graph,
    resume_session_with_value,
    run_session,
)
from backend.orchestrator.state import GraphState
from backend.persistence.db import init_db


# ── approval_node unit tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approval_node_full_auto_high_confidence_passes():
    """full-auto with confidence >= 0.7 should not interrupt."""
    state: GraphState = _make_state(approval_mode="full-auto", confidence=0.9)
    result = await approval_node(state)
    assert result == {}


@pytest.mark.asyncio
async def test_approval_node_full_auto_low_confidence_interrupts():
    """full-auto with confidence < 0.7 should call interrupt()."""
    from unittest.mock import patch as p
    state: GraphState = _make_state(approval_mode="full-auto", confidence=0.5)

    captured = {}

    def fake_interrupt(payload):
        captured["payload"] = payload
        # Simulate user approving
        return {"approved": True}

    with p("backend.orchestrator.graph.interrupt", fake_interrupt):
        result = await approval_node(state)

    assert captured["payload"]["reason"] == "low_confidence"
    assert captured["payload"]["confidence"] == 0.5
    assert result == {}


@pytest.mark.asyncio
async def test_approval_node_checkpoint_mode_always_interrupts():
    """checkpoint mode should always interrupt regardless of confidence."""
    from unittest.mock import patch as p
    state: GraphState = _make_state(approval_mode="checkpoint", confidence=0.95)

    captured = {}

    def fake_interrupt(payload):
        captured["payload"] = payload
        return {"approved": True}

    with p("backend.orchestrator.graph.interrupt", fake_interrupt):
        result = await approval_node(state)

    assert captured["payload"]["reason"] == "approval_mode"
    assert result == {}


@pytest.mark.asyncio
async def test_approval_node_rejection_sets_flag():
    """When user rejects, approval_rejected should be True."""
    from unittest.mock import patch as p
    state: GraphState = _make_state(approval_mode="checkpoint", confidence=0.9)

    with p("backend.orchestrator.graph.interrupt", lambda _: {"approved": False}):
        result = await approval_node(state)

    assert result == {"approval_rejected": True}


@pytest.mark.asyncio
async def test_approval_node_accepts_modified_composition():
    """User can pass back a modified team composition."""
    from unittest.mock import patch as p
    state: GraphState = _make_state(approval_mode="checkpoint", confidence=0.9)
    new_comp = {"team": [{"role": "Thinker", "model": "claude:sonnet", "count": 1, "passive": False}],
                "confidence": 0.9, "rationale": "user modified"}

    with p("backend.orchestrator.graph.interrupt", lambda _: {"approved": True, "team_composition": new_comp}):
        result = await approval_node(state)

    assert result == {"team_composition": new_comp}


# ── graph interrupt integration tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_session_checkpoint_returns_session_interrupt(tmp_path):
    """run_session with checkpoint mode should return SessionInterrupt (not run agents)."""
    db = tmp_path / "test.db"
    await init_db(db)

    _patch_planner_high_confidence()

    with _patch_planner():
        result = await run_session(
            session_id="sess-chk",
            agent_id="a1",
            task="test task",
            model="claude:sonnet",
            worktree_path=str(tmp_path),
            max_turns=5,
            db_path=db,
            approval_mode="checkpoint",
        )

    assert isinstance(result, SessionInterrupt)
    assert result.session_id == "sess-chk"
    assert result.payload["type"] == "team_approval"
    assert "team_composition" in result.payload


@pytest.mark.asyncio
async def test_run_session_full_auto_low_confidence_returns_interrupt(tmp_path):
    """full-auto with Planner confidence < 0.7 should also interrupt."""
    db = tmp_path / "test.db"
    await init_db(db)

    with _patch_planner(confidence=0.5):
        result = await run_session(
            session_id="sess-lowconf",
            agent_id="a1",
            task="test task",
            model="claude:sonnet",
            worktree_path=str(tmp_path),
            max_turns=5,
            db_path=db,
            approval_mode="full-auto",
        )

    assert isinstance(result, SessionInterrupt)
    assert result.payload["confidence"] == 0.5


@pytest.mark.asyncio
async def test_resume_with_rejection_returns_cancelled(tmp_path):
    """Resuming with approved=False should return a cancelled AgentResult."""
    db = tmp_path / "test.db"
    await init_db(db)

    with _patch_planner():
        # Start session — will interrupt at approval
        await run_session(
            session_id="sess-rej",
            agent_id="a1",
            task="test task",
            model="claude:sonnet",
            worktree_path=str(tmp_path),
            max_turns=5,
            db_path=db,
            approval_mode="checkpoint",
        )

    # Resume with rejection
    with _patch_planner():
        result = await resume_session_with_value(
            session_id="sess-rej",
            resume_value={"approved": False},
            db_path=db,
        )

    assert result["status"] == "cancelled"
    assert result["error"] == "Task cancelled by user"


@pytest.mark.asyncio
async def test_resume_with_approval_continues_to_spawn(tmp_path):
    """Resuming with approved=True should continue past the approval gate."""
    db = tmp_path / "test.db"
    await init_db(db)

    # Interrupt at approval
    with _patch_planner():
        await run_session(
            session_id="sess-ok",
            agent_id="a1",
            task="test task",
            model="claude:sonnet",
            worktree_path=str(tmp_path),
            max_turns=5,
            db_path=db,
            approval_mode="checkpoint",
        )

    # Resume with approval — patch spawn+run+review so we don't hit real workers
    with _patch_planner(), _patch_spawn_and_run():
        result = await resume_session_with_value(
            session_id="sess-ok",
            resume_value={"approved": True},
            db_path=db,
        )

    assert result["status"] in ("completed", "failed")
    assert result["agent_id"] == "orchestrator"


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_state(approval_mode: str, confidence: float) -> GraphState:
    return {
        "session_id": "test-sess",
        "task": "test task",
        "project_path": "/tmp",
        "agent_id": "a1",
        "model": "claude:sonnet",
        "worktree_path": "/tmp",
        "max_turns": 5,
        "team_composition": {
            "team": [{"role": "Builder", "model": "claude:sonnet", "count": 1, "passive": False}],
            "confidence": confidence,
            "rationale": "test",
        },
        "spawn_plan": None,
        "worker_results": {},
        "review_report": None,
        "result": None,
        "messages": [],
        "approval_mode": approval_mode,
        "approval_rejected": False,
    }


def _patch_planner(confidence: float = 0.85):
    """Patch plan_node to return a canned team composition without calling LLM."""
    from unittest.mock import patch, AsyncMock
    import backend.orchestrator.graph as gmod

    async def fake_plan(state):
        return {
            "team_composition": {
                "team": [{"role": "Builder", "model": "claude:sonnet", "count": 1, "passive": False}],
                "confidence": confidence,
                "rationale": "mocked",
            }
        }

    return patch.object(gmod, "plan_node", fake_plan)


def _patch_planner_high_confidence():
    pass  # kept for readability, actual patch done inside the test


def _patch_spawn_and_run():
    """Patch spawn+run+review nodes to return a minimal completed result without real workers."""
    import backend.orchestrator.graph as gmod
    from unittest.mock import patch, AsyncMock

    async def fake_spawn(state):
        return {
            "spawn_plan": {
                "session_id": state["session_id"],
                "project_path": state.get("project_path", "/tmp"),
                "active_agents": [],
                "passive_agents": [],
            }
        }

    async def fake_run(state):
        return {"worker_results": {}}

    async def fake_review(state):
        from backend.orchestrator.state import AgentResult
        return {
            "review_report": {"notes": [], "success": True, "total_commits_merged": 0, "failed_agents": []},
            "result": AgentResult(
                agent_id="orchestrator", status="completed", text_output="mocked output",
                input_tokens=0, output_tokens=0, cost_usd=0.0, error=None,
            ),
        }

    # Stack patches
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch.object(gmod, "spawn_node", fake_spawn))
    stack.enter_context(patch.object(gmod, "run_workers_node", fake_run))
    stack.enter_context(patch.object(gmod, "review_node", fake_review))
    return stack
