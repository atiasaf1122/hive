"""D4 — mechanical file-overlap resolution at plan parse time."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.orchestrator.graph import run_workers_node
from backend.orchestrator.nodes.planner import _parse_composition_dict


def _plan(team: list[dict]) -> dict:
    return {"team": team, "confidence": 0.9, "rationale": "t"}


def test_disjoint_hints_stay_parallel() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "app",
         "files_hint": ["app.py"]},
        {"role": "Tester", "model": "claude:sonnet", "subtask": "tests",
         "files_hint": ["tests/"]},
    ]))
    assert [m.wave for m in comp.team] == [0, 0]
    assert all(not m.predecessor_note for m in comp.team)


def test_same_role_overlap_merges() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "routes",
         "files_hint": ["app.py"], "max_turns": 10},
        {"role": "Builder", "model": "claude:sonnet", "subtask": "models",
         "files_hint": ["app.py", "db.py"], "max_turns": 15,
         "mcp_servers": ["context7"]},
    ]))
    assert len(comp.team) == 1
    merged = comp.team[0]
    assert "routes" in merged.subtask and "Additionally: models" in merged.subtask
    assert set(merged.files_hint) == {"app.py", "db.py"}
    assert merged.max_turns == 15
    assert merged.mcp_servers == ["context7"]


def test_cross_role_overlap_sequentializes() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "write app",
         "files_hint": ["app.py"]},
        {"role": "Tester", "model": "claude:sonnet", "subtask": "test app",
         "files_hint": ["app.py", "test_app.py"]},
    ]))
    assert len(comp.team) == 2
    builder, tester = comp.team
    assert builder.wave == 0
    assert tester.wave == 1
    assert "Builder" in tester.predecessor_note
    assert "app.py" in tester.predecessor_note


def test_dir_prefix_and_glob_overlap_detected() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "a",
         "files_hint": ["src/"]},
        {"role": "Tester", "model": "claude:sonnet", "subtask": "b",
         "files_hint": ["src/api/routes.py"]},
    ]))
    assert comp.team[1].wave == 1

    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "a",
         "files_hint": ["*.py"]},
        {"role": "Editor", "model": "claude:sonnet", "subtask": "b",
         "files_hint": ["main.py"]},
    ]))
    assert comp.team[1].wave == 1


def test_empty_hints_exempt() -> None:
    """Vague plans don't get fake precision — no check without hints."""
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "a"},
        {"role": "Tester", "model": "claude:sonnet", "subtask": "b"},
    ]))
    assert [m.wave for m in comp.team] == [0, 0]


@pytest.mark.asyncio
async def test_waves_respected_at_execution() -> None:
    """Wave-1 agents run strictly after wave-0 completes, with the
    predecessor note in their prompt."""
    order: list[str] = []
    prompts: dict[str, str] = {}

    async def fake_execute(agent, prompt, session_id, max_turns):
        order.append(agent.agent_id)
        prompts[agent.agent_id] = prompt
        return {"agent_id": agent.agent_id, "status": "completed", "text_output": "",
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": None}

    state = {
        "spawn_plan": {"active_agents": [
            {"agent_id": "tester-0", "role": "Tester", "model": "claude:sonnet",
             "worktree_path": "/tmp/t", "subtask": "test it", "wave": 1,
             "predecessor_note": "A Builder agent works on app.py before you"},
            {"agent_id": "builder-0", "role": "Builder", "model": "claude:sonnet",
             "worktree_path": "/tmp/b", "subtask": "build it", "wave": 0},
        ]},
        "pending_message": "go", "task": "go",
        "session_id": "s-d4", "max_turns": 20,
    }
    with patch("backend.orchestrator.graph._execute_worker", side_effect=fake_execute):
        out = await run_workers_node(state)  # type: ignore[arg-type]

    assert order == ["builder-0", "tester-0"]          # wave order, not list order
    assert "Predecessor work" in prompts["tester-0"]
    assert "Predecessor work" not in prompts["builder-0"]
    assert set(out["worker_results"]) == {"builder-0", "tester-0"}
