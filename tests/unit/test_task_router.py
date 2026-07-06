"""E3 — task-shape router: classification, override, solo synthesis,
chat path, fail-open, event recording."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.models_local import LocalModel, estimate_vram_mb
from backend.orchestrator.task_router import (
    ShapeDecision,
    _parse,
    build_solo_composition,
    resolve_task_shape,
)


# ── parsing & classification ────────────────────────────────────────────────


def test_parse_accepts_thinking_wrapped_json() -> None:
    raw = ('<think>hmm</think>{"shape": "solo", "role": "editor", '
           '"mechanical": true, "reason": "one-file typo fix"}')
    d = _parse(raw, engine="local:qwen3:8b")
    assert d.shape == "solo" and d.role == "Editor" and d.mechanical


def test_parse_rejects_garbage_and_bad_shape() -> None:
    assert _parse("not json at all", "e") is None
    assert _parse('{"shape": "fleet"}', "e") is None


@pytest.mark.asyncio
async def test_override_wins_without_any_model_call() -> None:
    with patch("backend.orchestrator.task_router._classify",
               new=AsyncMock(side_effect=AssertionError("must not classify"))):
        d = await resolve_task_shape("whatever", override="chat")
    assert d.shape == "chat" and d.engine == "override"


@pytest.mark.asyncio
async def test_classifier_failure_fails_open_to_swarm() -> None:
    with patch("backend.orchestrator.task_router._classify",
               new=AsyncMock(side_effect=RuntimeError("all backends down"))):
        d = await resolve_task_shape("build me a thing", override="auto")
    assert d.shape == "swarm" and d.engine == "fallback"


@pytest.mark.asyncio
async def test_local_classifier_preferred_when_available() -> None:
    pool = [LocalModel("qwen3:8b", 5.2, frozenset({"classification"}), "t",
                       estimate_vram_mb(5.2), available=True)]
    reply = json.dumps({"shape": "chat", "reason": "question"})
    with patch("backend.models_local.discover_local_models",
               new=AsyncMock(return_value=pool)), \
         patch("backend.orchestrator.task_router._ollama_generate",
               new=AsyncMock(return_value=reply)) as gen, \
         patch("backend.orchestrator.task_router._haiku",
               new=AsyncMock(side_effect=AssertionError("haiku not needed"))):
        d = await resolve_task_shape("what does the reviewer do?", "auto")
    assert d.shape == "chat" and d.engine == "local:qwen3:8b"
    gen.assert_awaited_once()


@pytest.mark.asyncio
async def test_haiku_fallback_when_no_local_pool() -> None:
    reply = json.dumps({"shape": "swarm", "reason": "multi-file"})
    with patch("backend.models_local.discover_local_models",
               new=AsyncMock(return_value=[])), \
         patch("backend.orchestrator.task_router._haiku",
               new=AsyncMock(return_value=reply)):
        d = await resolve_task_shape("refactor the api + docs + tests", "auto")
    assert d.shape == "swarm" and d.engine == "claude:haiku"


# ── solo synthesis ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_solo_mechanical_routes_local_coder() -> None:
    pool = [LocalModel("qwen3-coder:30b", 18.6, frozenset({"coding"}), "t",
                       estimate_vram_mb(18.6), available=True)]
    decision = ShapeDecision(shape="solo", reasoning="rename", engine="x",
                             role="Builder", mechanical=True)
    with patch("backend.models_local.discover_local_models",
               new=AsyncMock(return_value=pool)):
        comp = await build_solo_composition("rename foo to bar in cfg.py", decision)
    assert len(comp.team) == 1
    member = comp.team[0]
    assert member.model == "ollama:qwen3-coder:30b"
    assert member.fallback == "haiku"
    assert member.subtask == "rename foo to bar in cfg.py"


@pytest.mark.asyncio
async def test_solo_nonmechanical_stays_on_sonnet() -> None:
    decision = ShapeDecision(shape="solo", reasoning="needs context", engine="x",
                             role="Builder", mechanical=False)
    with patch("backend.models_local.discover_local_models",
               new=AsyncMock(side_effect=AssertionError("not consulted"))):
        comp = await build_solo_composition("add a null check where it crashes", decision)
    assert comp.team[0].model == "claude:sonnet"


@pytest.mark.asyncio
async def test_solo_mechanical_without_local_pool_uses_haiku() -> None:
    decision = ShapeDecision(shape="solo", reasoning="typo", engine="x",
                             role="Writer", mechanical=True)
    with patch("backend.models_local.discover_local_models",
               new=AsyncMock(return_value=[])):
        comp = await build_solo_composition("fix typo in README", decision)
    assert comp.team[0].model == "claude:haiku"
    assert comp.team[0].role == "Writer"


# ── graph integration: shapes route correctly ───────────────────────────────


def _graph_state(msg: str, shape: str = "auto") -> dict:
    return {
        "session_id": "sess-shape", "task": msg, "pending_message": msg,
        "project_path": "/tmp", "worktree_path": "/tmp", "db_path": "/tmp/x.db",
        "conversation_history": [], "state_doc": "",
        "turns_since_compaction": 0, "task_shape": shape,
        "approval_mode": "full-auto", "max_turns": 20,
    }


@pytest.mark.asyncio
async def test_chat_shape_answers_without_planner_or_spawn() -> None:
    from backend.orchestrator import graph as gmod

    with patch("backend.orchestrator.task_router.resolve_task_shape",
               new=AsyncMock(return_value=ShapeDecision(
                   shape="chat", reasoning="question", engine="local:qwen3:8b"))), \
         patch("backend.orchestrator.task_router.answer_chat",
               new=AsyncMock(return_value="It merges branches.")), \
         patch.object(gmod, "orchestrate",
                      new=AsyncMock(side_effect=AssertionError("planner must not run"))), \
         patch.object(gmod, "write_event", new=AsyncMock()), \
         patch.object(gmod, "_emit_to_ws", new=AsyncMock()):
        out = await gmod.orchestrator_node(_graph_state("what does the reviewer do?"))

    assert out["last_response"] == "It merges branches."
    assert (out["team_composition"] or {}).get("team") == []


@pytest.mark.asyncio
async def test_solo_shape_skips_planner_but_builds_team() -> None:
    from backend.orchestrator import graph as gmod

    with patch("backend.orchestrator.task_router.resolve_task_shape",
               new=AsyncMock(return_value=ShapeDecision(
                   shape="solo", reasoning="one file", engine="claude:haiku",
                   role="Builder", mechanical=False))), \
         patch.object(gmod, "orchestrate",
                      new=AsyncMock(side_effect=AssertionError("planner must not run"))), \
         patch.object(gmod, "write_event", new=AsyncMock()), \
         patch.object(gmod, "_emit_to_ws", new=AsyncMock()), \
         patch("backend.orchestrator.estimator.estimate_plan",
               new=AsyncMock(return_value=None)):
        out = await gmod.orchestrator_node(_graph_state("fix the typo in cfg.py"))

    team = out["team_composition"]["team"]
    assert len(team) == 1 and team[0]["model"] == "claude:sonnet"
    assert team[0]["subtask"] == "fix the typo in cfg.py"
    # solo path skips the plan gate: no plan_check attached
    assert "plan_check" not in out["team_composition"]


@pytest.mark.asyncio
async def test_swarm_shape_uses_full_planner_path() -> None:
    from backend.orchestrator import graph as gmod
    from backend.orchestrator.nodes.planner import (
        OrchestratorDecision,
        TeamComposition,
    )

    planner_decision = OrchestratorDecision(
        response="planning", composition=TeamComposition(team=[], confidence=1.0,
                                                         rationale="chat"))
    with patch("backend.orchestrator.task_router.resolve_task_shape",
               new=AsyncMock(return_value=ShapeDecision(
                   shape="swarm", reasoning="multi-file", engine="claude:haiku"))), \
         patch.object(gmod, "orchestrate",
                      new=AsyncMock(return_value=planner_decision)) as orch, \
         patch.object(gmod, "write_event", new=AsyncMock()), \
         patch.object(gmod, "_emit_to_ws", new=AsyncMock()):
        await gmod.orchestrator_node(_graph_state("build the whole feature"))
    orch.assert_awaited_once()


@pytest.mark.asyncio
async def test_shape_decision_recorded_as_event() -> None:
    from backend.orchestrator import graph as gmod

    events: list = []

    async def capture(ev, **kw):
        events.append(ev)

    with patch("backend.orchestrator.task_router.resolve_task_shape",
               new=AsyncMock(return_value=ShapeDecision(
                   shape="chat", reasoning="question", engine="override"))), \
         patch("backend.orchestrator.task_router.answer_chat",
               new=AsyncMock(return_value="answer")), \
         patch.object(gmod, "write_event", capture), \
         patch.object(gmod, "_emit_to_ws", new=AsyncMock()):
        await gmod.orchestrator_node(_graph_state("hello", shape="chat"))

    shape_events = [e for e in events if str(e.type) == "task/shape"]
    assert len(shape_events) == 1
    payload = shape_events[0].raw_payload
    assert payload["shape"] == "chat" and payload["override"] is True
