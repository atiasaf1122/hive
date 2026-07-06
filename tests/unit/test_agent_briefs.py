"""B1 — per-agent subtask briefs: planner schema, prompt building, max_turns."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.orchestrator.graph import _build_agent_prompt, run_workers_node
from backend.orchestrator.nodes.planner import _parse_composition_dict
from backend.orchestrator.nodes.spawner import SpawnedAgent


def _plan(team: list[dict]) -> dict:
    return {"team": team, "confidence": 0.9, "rationale": "test"}


# ── planner parsing ─────────────────────────────────────────────────────────


def test_parser_reads_per_agent_briefs() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet",
         "subtask": "Implement app.py", "files_hint": ["app.py"], "max_turns": 12},
        {"role": "Tester", "model": "claude:sonnet",
         "subtask": "Write pytest suite", "files_hint": ["tests/"], "max_turns": 8},
    ]))
    assert len(comp.team) == 2
    assert comp.team[0].subtask == "Implement app.py"
    assert comp.team[0].files_hint == ["app.py"]
    assert comp.team[0].max_turns == 12
    assert comp.team[1].subtask == "Write pytest suite"
    assert comp.team[0].subtask != comp.team[1].subtask


def test_parser_expands_legacy_count_into_individuals() -> None:
    """Old checkpoints/plans with count>1 become N single-agent members."""
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "count": 3, "subtask": "same brief"},
    ]))
    assert len(comp.team) == 3
    assert all(m.count == 1 for m in comp.team)
    assert comp.team[0].subtask == "same brief"


def test_parser_clamps_max_turns() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "x", "max_turns": 500},
    ]))
    assert comp.team[0].max_turns == 50


def test_parser_floors_max_turns() -> None:
    """E0.3: a plan-assigned budget below a working minimum starves the
    agent (tiny-fix builder died at 6 — every tool call costs a turn)."""
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "x", "max_turns": 3},
    ]))
    assert comp.team[0].max_turns == 10


def test_parser_defaults_when_briefs_absent() -> None:
    comp = _parse_composition_dict(_plan([{"role": "Builder", "model": "claude:sonnet"}]))
    assert comp.team[0].subtask == ""
    assert comp.team[0].files_hint is None
    assert comp.team[0].max_turns is None


# ── prompt building ─────────────────────────────────────────────────────────


def _agent(**kw) -> SpawnedAgent:
    base = dict(agent_id="a1", role="Builder", model="claude:sonnet",
                worktree_path="/tmp/wt")
    base.update(kw)
    return SpawnedAgent(**base)


def test_agent_prompt_contains_own_subtask_and_goal() -> None:
    prompt = _build_agent_prompt(
        _agent(subtask="Implement the /todos routes", files_hint=["app.py"]),
        goal="Build a Flask todo API", pending="Build a Flask todo API",
    )
    assert "Implement the /todos routes" in prompt
    assert "Build a Flask todo API" in prompt
    assert "app.py" in prompt
    assert "Builder" in prompt


def test_agent_prompt_falls_back_to_request_without_subtask() -> None:
    prompt = _build_agent_prompt(_agent(), goal="Fix the bug", pending="Fix the bug")
    assert "## Your subtask\nFix the bug" in prompt


def test_two_agents_get_different_prompts() -> None:
    a = _build_agent_prompt(_agent(subtask="Routes"), goal="g", pending="g")
    b = _build_agent_prompt(_agent(role="Tester", subtask="Tests"), goal="g", pending="g")
    assert a != b
    assert "Routes" in a and "Routes" not in b
    assert "Tests" in b and "Tests" not in a


# ── run_workers_node dispatch ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_workers_dispatches_distinct_prompts_and_turns() -> None:
    """Each agent must be executed with ITS OWN prompt and max_turns."""
    state = {
        "spawn_plan": {
            "active_agents": [
                {"agent_id": "builder-x-0", "role": "Builder", "model": "claude:sonnet",
                 "worktree_path": "/tmp/w0", "subtask": "Implement app.py",
                 "files_hint": ["app.py"], "max_turns": 12},
                {"agent_id": "tester-x-0", "role": "Tester", "model": "claude:sonnet",
                 "worktree_path": "/tmp/w1", "subtask": "Write the tests",
                 "files_hint": ["tests/"], "max_turns": 8},
            ],
        },
        "pending_message": "Build a Flask todo API with tests",
        "task": "Build a Flask todo API with tests",
        "session_id": "sess-b1",
        "max_turns": 20,
    }

    calls: list[tuple[str, str, int]] = []

    async def fake_execute(agent, prompt, session_id, max_turns):
        calls.append((agent.agent_id, prompt, max_turns))
        return {"agent_id": agent.agent_id, "status": "completed", "text_output": "",
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": None}

    with patch("backend.orchestrator.graph._execute_worker", side_effect=fake_execute):
        out = await run_workers_node(state)  # type: ignore[arg-type]

    assert set(out["worker_results"]) == {"builder-x-0", "tester-x-0"}
    prompts = {aid: p for aid, p, _ in calls}
    turns = {aid: t for aid, _, t in calls}
    assert prompts["builder-x-0"] != prompts["tester-x-0"]
    assert "Implement app.py" in prompts["builder-x-0"]
    assert "Write the tests" in prompts["tester-x-0"]
    assert turns["builder-x-0"] == 12
    assert turns["tester-x-0"] == 8


@pytest.mark.asyncio
async def test_perspective_diversity_same_subtask_still_works() -> None:
    """Same subtask with distinct lenses is a legitimate plan shape."""
    state = {
        "spawn_plan": {
            "active_agents": [
                {"agent_id": "r-0", "role": "Researcher", "model": "claude:sonnet",
                 "worktree_path": "/tmp/w0",
                 "subtask": "Why does login fail? Investigate from the DB angle."},
                {"agent_id": "r-1", "role": "Researcher", "model": "claude:sonnet",
                 "worktree_path": "/tmp/w1",
                 "subtask": "Why does login fail? Investigate from the network angle."},
            ],
        },
        "pending_message": "login fails",
        "task": "login fails",
        "session_id": "sess-b1b",
        "max_turns": 20,
    }

    prompts: list[str] = []

    async def fake_execute(agent, prompt, session_id, max_turns):
        prompts.append(prompt)
        return {"agent_id": agent.agent_id, "status": "completed", "text_output": "",
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": None}

    with patch("backend.orchestrator.graph._execute_worker", side_effect=fake_execute):
        await run_workers_node(state)  # type: ignore[arg-type]

    assert len(prompts) == 2 and prompts[0] != prompts[1]
    assert "DB angle" in prompts[0] + prompts[1]
    assert "network angle" in prompts[0] + prompts[1]


@pytest.mark.asyncio
async def test_same_role_members_get_distinct_agent_ids(tmp_path) -> None:
    """Phase D e2e finding: two same-role single-count members collided on
    agent_id (per-member index was always 0) — the second worktree failed
    and its subtask was silently dropped."""
    from unittest.mock import AsyncMock, patch

    from backend.orchestrator.nodes.planner import TeamComposition, TeamMember
    from backend.orchestrator.nodes.spawner import spawn_agents

    comp = TeamComposition(
        team=[
            TeamMember(role="Writer", model="claude:sonnet", subtask="a",
                       files_hint=["a.md"]),
            TeamMember(role="Writer", model="claude:sonnet", subtask="b",
                       files_hint=["b.md"]),
        ],
        confidence=0.9, rationale="r")

    created: list[str] = []

    class FakeManager:
        def __init__(self, session_id, project_path): ...

        async def create(self, agent_id, branch_name=None):
            created.append(agent_id)
            return tmp_path / agent_id

    with patch("backend.orchestrator.nodes.spawner.WorktreeManager", FakeManager), \
         patch("backend.orchestrator.nodes.spawner.create_agent",
               new_callable=AsyncMock):
        plan = await spawn_agents("sess-col", "task", comp, str(tmp_path))

    assert len(plan.active_agents) == 2
    ids = [a.agent_id for a in plan.active_agents]
    assert len(set(ids)) == 2, f"agent ids collided: {ids}"
    assert set(created) == set(ids)
