"""D2 — plan-quality gate: score, revision round, approval forcing, fail-open."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.orchestrator.graph import approval_node, orchestrator_node
from backend.orchestrator.nodes.planner import (
    OrchestratorDecision,
    TeamComposition,
    TeamMember,
)
from backend.orchestrator.plan_gate import PlanScore, score_plan


class FakeCaller:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        return self.responses.pop(0)


_PLAN = {"team": [{"role": "Builder", "model": "claude:sonnet",
                   "subtask": "do it", "files_hint": ["a.py"],
                   "max_turns": 10, "mcp_servers": []}],
         "confidence": 0.9, "rationale": "r"}


# ── score_plan ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_good_plan_passes() -> None:
    caller = FakeCaller([json.dumps({"score": 9, "issues": []})])
    check = await score_plan(_PLAN, "build it", haiku_caller=caller)
    assert check.passed and check.score == 9 and check.issues == []


@pytest.mark.asyncio
async def test_bad_plan_flags_issues() -> None:
    caller = FakeCaller([json.dumps(
        {"score": 4, "issues": ["Tester and Builder overlap on api.py"]})])
    check = await score_plan(_PLAN, "build it", haiku_caller=caller)
    assert not check.passed
    assert "overlap" in check.issues[0]


@pytest.mark.asyncio
async def test_gate_fails_open_on_error() -> None:
    async def boom(prompt):
        raise RuntimeError("haiku down")

    check = await score_plan(_PLAN, "build it", haiku_caller=boom)
    assert check.passed and check.score == 10


# ── orchestrator revision round + approval forcing ──────────────────────────


def _decision(subtask: str = "do it") -> OrchestratorDecision:
    return OrchestratorDecision(
        response="ok",
        composition=TeamComposition(
            team=[TeamMember(role="Builder", model="claude:sonnet",
                             subtask=subtask)],
            confidence=0.9, rationale="r"),
    )


def _state() -> dict:
    return {"session_id": "s-gate", "task": "build it",
            "pending_message": "build it", "conversation_history": [],
            "project_path": "/tmp"}


@pytest.mark.asyncio
async def test_good_plan_no_revision() -> None:
    with patch("backend.orchestrator.graph.orchestrate",
               new_callable=AsyncMock, return_value=_decision()) as orch, \
         patch("backend.orchestrator.plan_gate.score_plan") as _, \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        # patch score_plan where the graph imports it (function-local import)
        with patch("backend.orchestrator.plan_gate.score_plan",
                   new_callable=AsyncMock, return_value=PlanScore(9, [])) as gate:
            out = await orchestrator_node(_state())  # type: ignore[arg-type]

    assert orch.await_count == 1                      # no revision round
    assert out["team_composition"]["plan_check"]["passed"] is True


@pytest.mark.asyncio
async def test_bad_plan_gets_one_revision_round() -> None:
    scores = [PlanScore(4, ["overlap on a.py"]), PlanScore(9, [])]

    async def fake_gate(*a, **kw):
        return scores.pop(0)

    with patch("backend.orchestrator.graph.orchestrate",
               new_callable=AsyncMock,
               side_effect=[_decision(), _decision("revised subtask")]) as orch, \
         patch("backend.orchestrator.plan_gate.score_plan", side_effect=fake_gate), \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        out = await orchestrator_node(_state())  # type: ignore[arg-type]

    assert orch.await_count == 2                      # exactly one revision
    revision_msg = orch.await_args_list[1].kwargs["message"]
    assert "overlap on a.py" in revision_msg          # issues fed back
    assert out["team_composition"]["plan_check"]["passed"] is True
    assert out["team_composition"]["team"][0]["subtask"] == "revised subtask"


@pytest.mark.asyncio
async def test_still_bad_after_revision_forces_approval() -> None:
    async def fake_gate(*a, **kw):
        return PlanScore(4, ["still overlapping"])

    with patch("backend.orchestrator.graph.orchestrate",
               new_callable=AsyncMock,
               side_effect=[_decision(), _decision()]) as orch, \
         patch("backend.orchestrator.plan_gate.score_plan", side_effect=fake_gate), \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        out = await orchestrator_node(_state())  # type: ignore[arg-type]

    assert orch.await_count == 2                      # revision capped at one
    comp = out["team_composition"]
    assert comp["plan_check"]["passed"] is False

    # full-auto + flagged plan → the approval interrupt fires with the issues.
    captured: dict = {}

    def fake_interrupt(payload):
        captured.update(payload)
        return {"approved": True}

    with patch("backend.orchestrator.graph.interrupt", side_effect=fake_interrupt):
        await approval_node({  # type: ignore[arg-type]
            "approval_mode": "full-auto", "team_composition": comp,
        })
    assert captured["reason"] == "plan_check"
    assert captured["plan_check"]["issues"] == ["still overlapping"]


@pytest.mark.asyncio
async def test_user_can_approve_flagged_plan() -> None:
    comp = {**_PLAN, "plan_check": {"score": 4, "issues": ["x"], "passed": False}}
    with patch("backend.orchestrator.graph.interrupt",
               return_value={"approved": True}):
        out = await approval_node({  # type: ignore[arg-type]
            "approval_mode": "full-auto", "team_composition": comp,
        })
    assert out == {}                                   # approved → proceed
