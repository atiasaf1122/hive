"""G4 — prove >2-wave execution ordering directly (the golden three-wave
spec correctly collapses to 2 waves when the planner merges same-role
steps, so the execution loop's 3-wave handling is proven here in isolation)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.orchestrator import graph as gmod


@pytest.mark.asyncio
async def test_three_waves_run_in_order() -> None:
    """Agents in wave N only start after every wave < N finished."""
    started: list[str] = []

    async def fake_execute(agent, prompt, session_id, max_turns):
        started.append(agent.agent_id)
        return {"agent_id": agent.agent_id, "status": "completed",
                "text_output": "", "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0, "error": None}

    plan = {"active_agents": [
        {"agent_id": "w2", "role": "Tester", "model": "claude:sonnet",
         "worktree_path": "/tmp/w2", "subtask": "verify", "wave": 2,
         "files_hint": None},
        {"agent_id": "w0", "role": "Builder", "model": "claude:sonnet",
         "worktree_path": "/tmp/w0", "subtask": "data", "wave": 0,
         "files_hint": None},
        {"agent_id": "w1", "role": "Builder", "model": "claude:sonnet",
         "worktree_path": "/tmp/w1", "subtask": "api", "wave": 1,
         "files_hint": None},
    ]}
    state = {"spawn_plan": plan, "pending_message": "build", "task": "build",
             "session_id": "sess", "max_turns": 12, "project_path": "/tmp"}

    with patch.object(gmod, "_execute_worker", side_effect=fake_execute), \
         patch.object(gmod, "_missing_consumed_input", new=AsyncMock(return_value=None)):
        out = await gmod.run_workers_node(state)

    # strict wave order: w0 (wave 0) → w1 (wave 1) → w2 (wave 2)
    assert started == ["w0", "w1", "w2"]
    assert set(out["worker_results"]) == {"w0", "w1", "w2"}
    assert all(r["status"] == "completed" for r in out["worker_results"].values())


@pytest.mark.asyncio
async def test_failfast_in_middle_wave_still_runs_later_waves() -> None:
    """A fail-fast in wave 1 (missing input) doesn't stop wave 2 from being
    reached — the wave loop is robust to a per-agent failure."""
    ran: list[str] = []

    async def fake_execute(agent, prompt, session_id, max_turns):
        ran.append(agent.agent_id)
        return {"agent_id": agent.agent_id, "status": "completed",
                "text_output": "", "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0, "error": None}

    async def fake_missing(agent, active, state):
        return "missing input x" if agent.agent_id == "w1" else None

    plan = {"active_agents": [
        {"agent_id": "w0", "role": "Builder", "model": "claude:sonnet",
         "worktree_path": "/tmp/w0", "subtask": "a", "wave": 0, "files_hint": None},
        {"agent_id": "w1", "role": "Builder", "model": "claude:sonnet",
         "worktree_path": "/tmp/w1", "subtask": "b", "wave": 1, "files_hint": None},
        {"agent_id": "w2", "role": "Tester", "model": "claude:sonnet",
         "worktree_path": "/tmp/w2", "subtask": "c", "wave": 2, "files_hint": None},
    ]}
    state = {"spawn_plan": plan, "pending_message": "x", "task": "x",
             "session_id": "sess", "max_turns": 12, "project_path": "/tmp"}

    with patch.object(gmod, "_execute_worker", side_effect=fake_execute), \
         patch.object(gmod, "_missing_consumed_input", side_effect=fake_missing), \
         patch.object(gmod, "write_event", new=AsyncMock()):
        out = await gmod.run_workers_node(state)

    assert ran == ["w0", "w2"]                     # w1 failed fast, never executed
    assert out["worker_results"]["w1"]["status"] == "failed"
    assert out["worker_results"]["w2"]["status"] == "completed"
