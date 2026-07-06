"""G3 — contract-first briefs: the shared interface is parsed and injected
into every teammate's prompt."""
from __future__ import annotations

from backend.orchestrator.graph import _build_agent_prompt
from backend.orchestrator.nodes.planner import _parse_composition_dict
from backend.orchestrator.nodes.spawner import SpawnedAgent


_CONTRACT = ("GET /todos -> 200 [{id,task,done}]; POST /todos {task} -> 201; "
             "DELETE /todos/<id> -> 200|404")


def _plan(team, contract=None):
    d = {"response": "ok", "team": team, "confidence": 0.9, "rationale": "r"}
    if contract is not None:
        d["contract"] = contract
    return d


def test_contract_parsed_onto_every_member() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "app.py",
         "files_hint": ["app.py"]},
        {"role": "Tester", "model": "claude:sonnet", "subtask": "tests",
         "files_hint": ["test_app.py"]},
    ], contract=_CONTRACT))
    assert all(m.contract == _CONTRACT for m in comp.team)


def test_no_contract_leaves_empty() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "x"},
    ]))
    assert comp.team[0].contract == ""


def test_contract_injected_into_prompt_binding() -> None:
    agent = SpawnedAgent(agent_id="b-0", role="Builder", model="claude:sonnet",
                         worktree_path="/tmp", subtask="build app.py",
                         contract=_CONTRACT)
    prompt = _build_agent_prompt(agent, goal="todo api", pending="build it")
    assert "Shared contract" in prompt and "BINDING" in prompt
    assert "POST /todos" in prompt


def test_prompt_has_no_contract_section_when_absent() -> None:
    agent = SpawnedAgent(agent_id="b-0", role="Builder", model="claude:sonnet",
                         worktree_path="/tmp", subtask="fix typo")
    prompt = _build_agent_prompt(agent, goal="g", pending="p")
    assert "Shared contract" not in prompt


def test_contract_survives_dict_roundtrip() -> None:
    from backend.orchestrator.graph import _agent_to_dict, _dict_to_agent
    a = SpawnedAgent(agent_id="b-0", role="Tester", model="claude:sonnet",
                     worktree_path="/tmp", contract=_CONTRACT)
    back = _dict_to_agent(_agent_to_dict(a))
    assert back.contract == _CONTRACT
