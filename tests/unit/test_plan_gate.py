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


# ── D6: pre-flight estimate ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_estimate_from_seeded_history(tmp_path) -> None:
    from backend.orchestrator.estimator import estimate_plan
    from backend.persistence.db import init_db
    from backend.persistence.events import (
        create_agent, create_session, write_cost, write_event)
    from backend.workers.base import EventType, HiveEvent

    db = tmp_path / "t.db"
    await init_db(db)
    # Seed 3 similar past sessions: 2 agents each, known cost + duration.
    for i, cost in enumerate([0.40, 0.60, 1.00]):
        sid = f"h{i}"
        await create_session(sid, db_path=db)
        for j in range(2):
            await create_agent(f"a{i}{j}", sid, role="Builder",
                               model="claude:sonnet", worktree_path="/w", db_path=db)
        await write_cost(sid, f"a{i}0", 100, 200, cost, db_path=db)
        await write_event(HiveEvent(type=EventType.AGENT_START, agent_id=f"a{i}0",
                                    session_id=sid, ts=1000.0), path=db)
        await write_event(HiveEvent(type=EventType.AGENT_END, agent_id=f"a{i}0",
                                    session_id=sid, ts=1000.0 + 300), path=db)

    plan = {"team": [{"role": "Builder", "model": "claude:sonnet"},
                     {"role": "Tester", "model": "claude:sonnet"}]}
    est = await estimate_plan(plan, db_path=db)
    assert est is not None
    assert est["based_on_sessions"] == 3
    assert est["cost_median_usd"] == 0.60
    assert est["cost_p90_usd"] >= 0.60
    assert est["duration_median_s"] == 300


@pytest.mark.asyncio
async def test_estimate_cold_start_returns_none(tmp_path) -> None:
    from backend.orchestrator.estimator import estimate_plan
    from backend.persistence.db import init_db

    db = tmp_path / "t.db"
    await init_db(db)
    plan = {"team": [{"role": "Builder", "model": "claude:sonnet"}]}
    assert await estimate_plan(plan, db_path=db) is None


@pytest.mark.asyncio
async def test_estimate_vs_actual_event_recorded() -> None:
    from backend.orchestrator.graph import review_node
    from backend.workers.base import EventType

    written = []

    async def fake_write(event, path=None):
        written.append(event)

    state = {
        "session_id": "s-e",
        "team_composition": {"team": [], "estimate": {
            "cost_median_usd": 0.5, "cost_p90_usd": 1.0,
            "duration_median_s": 300, "duration_p90_s": 500,
            "based_on_sessions": 3}},
        "spawn_plan": {"session_id": "s-e", "project_path": "/p",
                       "active_agents": [], "passive_agents": []},
        "worker_results": {"a": {"agent_id": "a", "status": "completed",
                                 "text_output": "t", "summary": "s",
                                 "input_tokens": 10, "output_tokens": 20,
                                 "cost_usd": 0.75, "error": None}},
        "conversation_history": [], "last_response": "",
    }
    fake_report = type("R", (), {"notes": [], "success": True,
                                 "total_commits_merged": 0,
                                 "failed_agents": [], "merged": [],
                                 "conflicts": []})()
    with patch("backend.orchestrator.graph.review_and_merge",
               new_callable=AsyncMock, return_value=fake_report), \
         patch("backend.orchestrator.graph.write_event", side_effect=fake_write), \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        await review_node(state)  # type: ignore[arg-type]

    est_events = [e for e in written if str(e.type) == str(EventType.ESTIMATE_ACTUAL)]
    assert len(est_events) == 1
    assert est_events[0].raw_payload["actual_cost_usd"] == 0.75
    assert est_events[0].raw_payload["estimate"]["based_on_sessions"] == 3
