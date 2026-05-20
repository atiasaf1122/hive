"""Tests for the live Haiku caller (Item 2 of the v1.0 plan).

We stub the underlying Worker so no real `claude` subprocess runs.
The stub yields the same event types ClaudeCLIWorker would, in the
same order, so the caller exercises every code path it cares about:
TEXT_DELTA accumulation, COST recording, AGENT_ERROR escalation,
and budget enforcement.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from backend.llm.haiku import HaikuBudgetExhausted, HaikuCaller
from backend.workers.base import EventType, HiveEvent, WorkerConfig


@dataclass
class _StubWorker:
    """Yields a scripted sequence of events for each `run()` call."""
    scripts: list[list[dict]]
    calls: list[WorkerConfig] = field(default_factory=list)
    killed: list[str] = field(default_factory=list)
    _idx: int = 0

    async def run(self, prompt: str, config: WorkerConfig) -> AsyncIterator[HiveEvent]:
        self.calls.append(config)
        script = self.scripts[self._idx]
        self._idx += 1
        for ev in script:
            kwargs = dict(ev)
            yield HiveEvent(
                agent_id=config.agent_id,
                session_id=config.session_id,
                **kwargs,
            )

    async def kill(self, agent_id: str) -> None:
        self.killed.append(agent_id)


def _text(s: str) -> dict:
    return {"type": EventType.TEXT_DELTA, "text": s}


def _cost(inp: int, out: int, usd: float = 0.001) -> dict:
    return {
        "type": EventType.COST,
        "input_tokens": inp, "output_tokens": out, "cost_usd": usd,
    }


def _error(msg: str) -> dict:
    return {"type": EventType.AGENT_ERROR, "error": msg}


@pytest.mark.asyncio
async def test_invoke_concatenates_text_deltas_and_strips():
    worker = _StubWorker(scripts=[[
        _text("Hel"), _text("lo "), _text("world"), _text("\n"),
        _cost(10, 5),
    ]])
    costs: list[tuple] = []

    async def fake_cost_write(session_id, agent_id, inp, out, usd):
        costs.append((session_id, agent_id, inp, out, usd))

    caller = HaikuCaller(
        worker=worker, session_id="sess-1",
        cost_writer=fake_cost_write,
    )
    out = await caller("anything")
    assert out == "Hello world"
    assert worker.calls[0].model == "claude:haiku"
    assert worker.calls[0].max_turns == 1
    assert costs == [("sess-1", worker.calls[0].agent_id, 10, 5, 0.001)]


@pytest.mark.asyncio
async def test_invoke_records_spend_in_caller():
    worker = _StubWorker(scripts=[[_text("ok"), _cost(100, 50, 0.005)]])
    caller = HaikuCaller(
        worker=worker, session_id="sess-2",
        cost_writer=lambda *a, **k: _noop(),
    )
    await caller("x")
    assert caller.spend.input_tokens == 100
    assert caller.spend.output_tokens == 50
    assert caller.spend.cost_usd == pytest.approx(0.005)
    assert caller.spend.calls == 1


async def _noop() -> None:
    return None


@pytest.mark.asyncio
async def test_invoke_handles_no_cost_event_gracefully():
    """Some streams may close without emitting a final cost event."""
    worker = _StubWorker(scripts=[[_text("alright")]])
    caller = HaikuCaller(
        worker=worker, session_id="sess-3",
        cost_writer=lambda *a, **k: _noop(),
    )
    out = await caller("x")
    assert out == "alright"
    assert caller.spend.calls == 0  # no cost row -> no spend recorded


@pytest.mark.asyncio
async def test_invoke_raises_on_agent_error():
    worker = _StubWorker(scripts=[[
        _text("starting…"),
        _error("Auth failure"),
    ]])
    caller = HaikuCaller(
        worker=worker, session_id="sess-4",
        cost_writer=lambda *a, **k: _noop(),
    )
    with pytest.raises(RuntimeError, match="Auth failure"):
        await caller("x")


@pytest.mark.asyncio
async def test_budget_exhausted_blocks_further_calls():
    worker = _StubWorker(scripts=[
        [_text("first"), _cost(60, 0)],
        [_text("second")],   # shouldn't get here
    ])
    caller = HaikuCaller(
        worker=worker, session_id="sess-5",
        cost_writer=lambda *a, **k: _noop(),
        budget_tokens=50,    # below first-call usage
    )
    await caller("first")  # consumes the budget
    with pytest.raises(HaikuBudgetExhausted):
        await caller("second")
    # Worker.run only invoked once (second attempt blocked before run).
    assert len(worker.calls) == 1


@pytest.mark.asyncio
async def test_invoke_kills_worker_on_oversize_response():
    """A rambling Haiku response should be cut off mid-stream."""
    long_chunk = "x" * 5_000
    worker = _StubWorker(scripts=[[
        _text(long_chunk),
        _text(long_chunk),    # triggers the cap at 8 000 chars
        _text(long_chunk),    # should never be yielded — kill() interrupts
        _cost(1, 1),
    ]])
    caller = HaikuCaller(
        worker=worker, session_id="sess-6",
        cost_writer=lambda *a, **k: _noop(),
        max_response_len=8_000,
    )
    out = await caller("x")
    assert len(out) == 10_000  # two chunks accumulated before the cap fired
    assert worker.killed, "kill() must be invoked when the response cap trips"


@pytest.mark.asyncio
async def test_remaining_tokens_reflects_spend():
    worker = _StubWorker(scripts=[[_text("ok"), _cost(70, 30)]])
    caller = HaikuCaller(
        worker=worker, session_id="sess-7",
        cost_writer=lambda *a, **k: _noop(),
        budget_tokens=200,
    )
    assert caller.remaining_tokens() == 200
    await caller("x")
    assert caller.remaining_tokens() == 100


# ── Integration with semantic_cross_check + maybe_rerank ────────────────────


@pytest.mark.asyncio
async def test_caller_slots_into_semantic_cross_check():
    """The validator's haiku_caller=callable shape works with HaikuCaller."""
    from backend.validation.schema import CompletionReport
    from backend.validation.validators import (
        ValidationContext, semantic_cross_check,
    )

    worker = _StubWorker(scripts=[[_text("8 evidence supports claim"),
                                   _cost(20, 10)]])
    caller = HaikuCaller(
        worker=worker, session_id="sess-x",
        cost_writer=lambda *a, **k: _noop(),
    )
    report = CompletionReport(status="done", description="Added login form")
    sem = await semantic_cross_check(report, ValidationContext(),
                                     haiku_caller=caller)
    assert sem.score == 8.0
    assert "supports claim" in sem.rationale


# ── HTTP-level wiring (POST /api/validation/cross-check) ───────────────────


def test_cross_check_endpoint_runs_validators_and_semantic(monkeypatch, tmp_path):
    """The cross-check route plumbs the body into the deterministic
    validators and the Haiku caller. We stub build_caller so no real
    subprocess runs, and use a tmp_path worktree with the actual file
    present so the FileCreationValidator is satisfied."""
    from fastapi.testclient import TestClient

    from backend.api import validation_http as vh
    from backend.main import app

    # Worktree with the claimed file actually present.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Login.tsx").write_text("// stub", encoding="utf-8")

    async def fake_caller(prompt: str) -> str:
        return "9 evidence matches files touched"

    def fake_build_caller(session_id, **kwargs):
        return fake_caller

    monkeypatch.setattr(vh, "build_caller", fake_build_caller)

    with TestClient(app) as client:
        resp = client.post("/api/validation/cross-check", json={
            "session_id": "sess-http",
            "report": {
                "status": "done",
                "description": "Added a login page",
                "evidence": {
                    "files_touched": [{
                        "path": "src/Login.tsx",
                        "action": "created",
                        "lines_added": 30,
                        "lines_removed": 0,
                    }],
                },
            },
            "worktree_path": str(tmp_path),
            "git_changes": [{
                "path": "src/Login.tsx", "is_new": True,
                "lines_added": 30, "lines_removed": 0,
            }],
            "audit_rows": [],
            "installed_packages_after": [],
            "run_semantic_check": True,
        })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deterministic"]["passed"] is True
    assert body["semantic"]["score"] == 9.0
    assert body["semantic"]["skipped"] is False


def test_cross_check_endpoint_can_skip_semantic(monkeypatch, tmp_path):
    """run_semantic_check=False keeps the call free of Haiku."""
    from fastapi.testclient import TestClient

    from backend.api import validation_http as vh
    from backend.main import app

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Login.tsx").write_text("// stub", encoding="utf-8")

    called = []
    def fake_build_caller(session_id, **kwargs):
        called.append(session_id)
        return lambda p: None  # never invoked

    monkeypatch.setattr(vh, "build_caller", fake_build_caller)

    with TestClient(app) as client:
        resp = client.post("/api/validation/cross-check", json={
            "session_id": "sess-skip",
            "report": {
                "status": "done",
                "description": "x",
                "evidence": {"files_touched": [{
                    "path": "src/Login.tsx", "action": "created",
                }]},
            },
            "worktree_path": str(tmp_path),
            "git_changes": [{"path": "src/Login.tsx", "is_new": True}],
            "run_semantic_check": False,
        })

    assert resp.status_code == 200
    assert resp.json()["semantic"] is None
    assert called == []  # build_caller never invoked


@pytest.mark.asyncio
async def test_caller_slots_into_maybe_rerank():
    """Same plumbing for skills rerank — the gate fires, the caller runs."""
    from backend.skills.registry import HybridHit, Skill, maybe_rerank

    worker = _StubWorker(scripts=[[_text("alpha\n"), _cost(15, 5)]])
    caller = HaikuCaller(
        worker=worker, session_id="sess-y",
        cost_writer=lambda *a, **k: _noop(),
    )
    hits = [
        HybridHit(skill=Skill(id="alpha", name="alpha", description="",
                              tags=[], path="", instructions="", version=1),
                  semantic=0.8, keyword=0.5, tag_match=0.0, combined=0.7),
        HybridHit(skill=Skill(id="bravo", name="bravo", description="",
                              tags=[], path="", instructions="", version=1),
                  semantic=0.6, keyword=0.4, tag_match=0.0, combined=0.5),
    ]
    result = await maybe_rerank(
        hits, query="hi", tech_stack=None,
        expected_agent_count=10,       # forces the gate
        haiku_caller=caller,
    )
    assert result.used_llm is True
    assert [h.skill.id for h in result.hits] == ["alpha"]
