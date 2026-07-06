"""D1 — lessons store: grounded write, gate, conservative retrieval, hygiene."""
from __future__ import annotations

import hashlib
from unittest.mock import patch

import numpy as np
import pytest

from backend.lessons.distiller import (
    GATE_THRESHOLD,
    DistillResult,
    GateResult,
    LessonDraft,
)
from backend.lessons.service import (
    _collect_triggers,
    distill_session_lessons,
    run_session_hygiene,
)
from backend.lessons.store import (
    get_lesson,
    list_lessons,
    record_application,
    render_lessons_section,
    resolve_applications,
    retrieve_lessons,
    save_lesson,
)
from backend.persistence.db import init_db
from backend.persistence.events import create_session, write_event
from backend.workers.base import EventType, HiveEvent


def _fake_embed(text: str) -> np.ndarray:
    """Deterministic embedding: identical text → identical vector; texts
    sharing the marker token 'BRANCHNAME' land close together. Zero-mean
    noise so UNRELATED texts sit near cosine 0 (uniform-positive vectors
    would share a ~0.75 baseline and defeat the threshold tests)."""
    rng = np.random.default_rng(
        int(hashlib.sha256(text.encode()).hexdigest()[:8], 16))
    vec = rng.standard_normal(64).astype(np.float32)
    if "BRANCHNAME" in text:
        vec[:48] = 3.0   # dominant shared direction
    vec /= np.linalg.norm(vec)
    return vec


@pytest.fixture(autouse=True)
def fake_embeddings():
    with patch("backend.lessons.store.embed", side_effect=_fake_embed), \
         patch("backend.lessons.service.embed", create=True, side_effect=_fake_embed), \
         patch("backend.skills.embedder.embed", side_effect=_fake_embed):
        yield


@pytest.fixture
async def db(tmp_path):
    p = tmp_path / "t.db"
    await init_db(p)
    await create_session("sess-l", db_path=p)
    return p


class FakeDistiller:
    """Deterministic distiller: returns a fixed draft; gate score settable."""

    def __init__(self, draft: LessonDraft | None, score: int = 10,
                 none_reason: str = "no lesson supported") -> None:
        self.draft = draft
        self.score = score
        self.none_reason = none_reason
        self.distill_calls: list[str] = []

    async def distill(self, evidence, *, origin):
        self.distill_calls.append(evidence)
        if self.draft is None:
            return DistillResult(None, reason=self.none_reason)
        return DistillResult(self.draft)

    async def gate(self, draft, evidence):
        return GateResult(self.score, reason=f"scored {self.score}")


_DRAFT = LessonDraft(
    title="Check default BRANCHNAME before diffing",
    description="Assumed 'main' when the repo used 'master'.",
    content="Git repos may use 'master' as default BRANCHNAME; resolve the "
            "actual default before computing merge-base diffs.",
    trigger_context="tasks that diff or merge against the default git BRANCHNAME",
    origin="agent",
)


# ── triggers (grounded-only write path) ─────────────────────────────────────


def _ev(etype, agent, payload, session="sess-l"):
    return {"ts": 0, "agent_id": agent, "type": str(etype), "payload": payload}


def test_validation_fixed_trigger_requires_later_clean_run() -> None:
    events = [
        _ev(EventType.VALIDATION_FAILED, "builder-0", {"findings": ["claims x, no git change"]}),
        _ev(EventType.AGENT_END, "builder-0", {}),
    ]
    triggers = _collect_triggers(events)
    assert len(triggers) == 1
    assert triggers[0].kind == "validation_fixed"
    assert "claims x" in triggers[0].evidence


def test_unresolved_validation_failure_is_not_a_trigger() -> None:
    """Failure with NO later clean run → no grounded resolution → no lesson."""
    events = [
        _ev(EventType.VALIDATION_FAILED, "builder-0", {"findings": ["claims x"]}),
    ]
    assert _collect_triggers(events) == []


def test_llm_review_and_infra_triggers() -> None:
    events = [
        _ev(EventType.REVIEW_LLM, "reviewer", {"notes": ["LLM review: merged branch, false negative"]}),
        _ev(EventType.AGENT_ERROR, "tester-0", {"error": "MCP preflight failed — x", "origin": "infrastructure"}),
        _ev(EventType.AGENT_ERROR, "tester-1", {"error": "boom", "origin": "unknown"}),
    ]
    kinds = [t.kind for t in _collect_triggers(events)]
    assert kinds == ["llm_review", "infrastructure"]   # unknown-origin excluded


# ── distillation + gate ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grounded_trigger_produces_saved_lesson(db) -> None:
    await write_event(HiveEvent(
        type=EventType.VALIDATION_FAILED, agent_id="b-0", session_id="sess-l",
        raw_payload={"findings": ["claims created x.py, no matching git change"]},
    ), path=db)
    await write_event(HiveEvent(
        type=EventType.AGENT_END, agent_id="b-0", session_id="sess-l"), path=db)

    distiller = FakeDistiller(_DRAFT, score=10)
    saved = await distill_session_lessons("sess-l", "/proj", distiller=distiller, db_path=db)
    assert len(saved) == 1
    lesson = await get_lesson(saved[0], db_path=db)
    assert lesson.title == _DRAFT.title
    assert lesson.scope == "project" and lesson.project_path == "/proj"
    assert "claims created x.py" in lesson.source_evidence


@pytest.mark.asyncio
async def test_distiller_none_saves_nothing(db) -> None:
    await write_event(HiveEvent(
        type=EventType.REVIEW_LLM, agent_id="reviewer", session_id="sess-l",
        raw_payload={"notes": ["inconclusive"]}), path=db)
    saved = await distill_session_lessons(
        "sess-l", "/proj", distiller=FakeDistiller(None), db_path=db)
    assert saved == []


@pytest.mark.asyncio
async def test_gate_discards_unsupported_lesson(db) -> None:
    await write_event(HiveEvent(
        type=EventType.REVIEW_LLM, agent_id="reviewer", session_id="sess-l",
        raw_payload={"notes": ["resolved something"]}), path=db)
    saved = await distill_session_lessons(
        "sess-l", "/proj",
        distiller=FakeDistiller(_DRAFT, score=GATE_THRESHOLD - 1), db_path=db)
    assert saved == []
    assert await list_lessons(db_path=db) == []
    # The discard is logged as an inspectable event.
    from backend.persistence.events import get_session_events
    events = await get_session_events("sess-l", path=db)
    assert any(e["type"] == str(EventType.LESSON_DISCARDED) for e in events)


# ── E0.1 audit trail: every attempt emits exactly one lesson/* event ────────


async def _lesson_events(db):
    from backend.persistence.events import get_session_events
    events = await get_session_events("sess-l", path=db)
    return [e for e in events if e["type"].startswith("lesson/")]


@pytest.mark.asyncio
async def test_stored_outcome_emits_lesson_stored_event(db) -> None:
    await write_event(HiveEvent(
        type=EventType.REVIEW_LLM, agent_id="reviewer", session_id="sess-l",
        raw_payload={"notes": ["resolved the conflict, root cause X"]}), path=db)
    saved = await distill_session_lessons(
        "sess-l", "/proj", distiller=FakeDistiller(_DRAFT, 9), db_path=db)
    evs = await _lesson_events(db)
    assert len(evs) == 1 and evs[0]["type"] == str(EventType.LESSON_STORED)
    payload = evs[0]["payload"]["raw_payload"]
    assert payload["lesson_id"] == saved[0] and payload["gate_score"] == 9


@pytest.mark.asyncio
async def test_none_outcome_emits_lesson_none_with_reason(db) -> None:
    await write_event(HiveEvent(
        type=EventType.REVIEW_LLM, agent_id="reviewer", session_id="sess-l",
        raw_payload={"notes": ["inconclusive"]}), path=db)
    await distill_session_lessons(
        "sess-l", "/proj",
        distiller=FakeDistiller(None, none_reason="evidence names no root cause"),
        db_path=db)
    evs = await _lesson_events(db)
    assert len(evs) == 1 and evs[0]["type"] == str(EventType.LESSON_NONE)
    assert evs[0]["payload"]["raw_payload"]["reason"] == "evidence names no root cause"


@pytest.mark.asyncio
async def test_discard_outcome_emits_event_with_score_and_reason(db) -> None:
    await write_event(HiveEvent(
        type=EventType.REVIEW_LLM, agent_id="reviewer", session_id="sess-l",
        raw_payload={"notes": ["resolved something"]}), path=db)
    await distill_session_lessons(
        "sess-l", "/proj",
        distiller=FakeDistiller(_DRAFT, GATE_THRESHOLD - 2), db_path=db)
    evs = await _lesson_events(db)
    assert len(evs) == 1 and evs[0]["type"] == str(EventType.LESSON_DISCARDED)
    payload = evs[0]["payload"]["raw_payload"]
    assert payload["gate_score"] == GATE_THRESHOLD - 2
    assert payload["draft_title"] == _DRAFT.title and payload["reason"]


@pytest.mark.asyncio
async def test_duplicate_outcome_emits_discard_event(db) -> None:
    await save_lesson(scope="project", project_path="/proj", title=_DRAFT.title,
                      description=_DRAFT.description, content=_DRAFT.content,
                      trigger_context=_DRAFT.trigger_context, origin="agent",
                      source_session="old", source_evidence="e", db_path=db)
    await write_event(HiveEvent(
        type=EventType.REVIEW_LLM, agent_id="reviewer", session_id="sess-l",
        raw_payload={"notes": ["same again"]}), path=db)
    await distill_session_lessons(
        "sess-l", "/proj", distiller=FakeDistiller(_DRAFT, 10), db_path=db)
    evs = await _lesson_events(db)
    assert len(evs) == 1 and evs[0]["type"] == str(EventType.LESSON_DISCARDED)
    assert "duplicate" in evs[0]["payload"]["raw_payload"]["reason"]


@pytest.mark.asyncio
async def test_distiller_exception_still_leaves_a_trail(db) -> None:
    """No silent path: even a crashing distiller emits LESSON_NONE."""

    class ExplodingDistiller:
        async def distill(self, evidence, *, origin):
            raise RuntimeError("model unreachable")

        async def gate(self, draft, evidence):
            raise AssertionError("unreached")

    await write_event(HiveEvent(
        type=EventType.REVIEW_LLM, agent_id="reviewer", session_id="sess-l",
        raw_payload={"notes": ["resolved"]}), path=db)
    saved = await distill_session_lessons(
        "sess-l", "/proj", distiller=ExplodingDistiller(), db_path=db)
    assert saved == []
    evs = await _lesson_events(db)
    assert len(evs) == 1 and evs[0]["type"] == str(EventType.LESSON_NONE)
    assert "model unreachable" in evs[0]["payload"]["raw_payload"]["reason"]


@pytest.mark.asyncio
async def test_duplicate_lesson_skipped(db) -> None:
    await save_lesson(scope="project", project_path="/proj", title=_DRAFT.title,
                      description=_DRAFT.description, content=_DRAFT.content,
                      trigger_context=_DRAFT.trigger_context, origin="agent",
                      source_session="old", source_evidence="e", db_path=db)
    await write_event(HiveEvent(
        type=EventType.REVIEW_LLM, agent_id="reviewer", session_id="sess-l",
        raw_payload={"notes": ["same again"]}), path=db)
    saved = await distill_session_lessons(
        "sess-l", "/proj", distiller=FakeDistiller(_DRAFT, 10), db_path=db)
    assert saved == []
    assert len(await list_lessons(db_path=db)) == 1


# ── retrieval (conservative) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retrieval_high_threshold_and_scope(db) -> None:
    lid = await save_lesson(scope="project", project_path="/proj",
                            title=_DRAFT.title, description=_DRAFT.description,
                            content=_DRAFT.content, trigger_context=_DRAFT.trigger_context,
                            origin="agent", source_session="s", source_evidence="e",
                            db_path=db)
    # Related query (shares the BRANCHNAME direction) → retrieved.
    hits = await retrieve_lessons("merge the BRANCHNAME work", project_path="/proj", db_path=db)
    assert [l.id for l in hits] == [lid]
    # Unrelated query → nothing (zero injections is normal).
    assert await retrieve_lessons("write a poem about clouds", project_path="/proj", db_path=db) == []
    # Someone else's project-scoped lesson → never injected.
    assert await retrieve_lessons("merge the BRANCHNAME work", project_path="/other", db_path=db) == []


@pytest.mark.asyncio
async def test_retrieval_cap(db) -> None:
    for i in range(5):
        await save_lesson(scope="global", project_path=None,
                          title=f"L{i} BRANCHNAME", description="d",
                          content=f"BRANCHNAME pitfall variant {i}",
                          trigger_context="BRANCHNAME tasks", origin="agent",
                          source_session="s", source_evidence="e", db_path=db)
    hits = await retrieve_lessons("BRANCHNAME work", project_path=None, db_path=db)
    assert len(hits) == 3   # MAX_LESSONS_PER_BRIEF


def test_render_section() -> None:
    assert render_lessons_section([]) == ""


# ── hygiene ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_and_archive_transitions(db) -> None:
    lid = await save_lesson(scope="global", project_path=None, title=_DRAFT.title,
                            description=_DRAFT.description, content=_DRAFT.content,
                            trigger_context=_DRAFT.trigger_context, origin="agent",
                            source_session="s", source_evidence="e", db_path=db)

    # Injection with NO recurrence → confirmed.
    await record_application("sess-l", lid, "b-0", db_path=db)
    out = await resolve_applications("sess-l", ["totally unrelated failure"], db_path=db)
    assert out == {lid: True}
    assert (await get_lesson(lid, db_path=db)).times_confirmed == 1

    # Three injections where the SAME failure class recurs → archived.
    for i in range(3):
        sid = f"sess-r{i}"
        await create_session(sid, db_path=db)
        await record_application(sid, lid, "b-0", db_path=db)
        out = await resolve_applications(
            sid, ["diff failed: wrong default BRANCHNAME assumed"], db_path=db)
        assert out == {lid: False}
    lesson = await get_lesson(lid, db_path=db)
    assert lesson.times_unconfirmed == 3
    assert lesson.status == "archived"


@pytest.mark.asyncio
async def test_session_hygiene_reads_failures_from_events(db) -> None:
    lid = await save_lesson(scope="global", project_path=None, title=_DRAFT.title,
                            description=_DRAFT.description, content=_DRAFT.content,
                            trigger_context=_DRAFT.trigger_context, origin="agent",
                            source_session="s", source_evidence="e", db_path=db)
    await record_application("sess-l", lid, "b-0", db_path=db)
    await write_event(HiveEvent(
        type=EventType.VALIDATION_FAILED, agent_id="b-0", session_id="sess-l",
        raw_payload={"findings": ["assumed default BRANCHNAME wrongly again"]},
    ), path=db)
    out = await run_session_hygiene("sess-l", db_path=db)
    assert out == {lid: False}


# ── injection lands in agent prompts ────────────────────────────────────────


@pytest.mark.asyncio
async def test_lessons_injected_into_agent_prompt(db) -> None:
    from unittest.mock import AsyncMock

    from backend.orchestrator.graph import run_workers_node

    # Uses the conftest-isolated DEFAULT DB — the graph's retrieval call
    # takes no db_path, and default args bind at def time so patching the
    # module constant wouldn't reach it.
    from backend.persistence.db import DB_PATH as default_db
    await init_db(default_db)
    await create_session("sess-l", db_path=default_db)
    await save_lesson(scope="project", project_path="/proj", title=_DRAFT.title,
                      description=_DRAFT.description, content=_DRAFT.content,
                      trigger_context=_DRAFT.trigger_context, origin="agent",
                      source_session="s", source_evidence="e")

    prompts: list[str] = []

    async def fake_execute(agent, prompt, session_id, max_turns):
        prompts.append(prompt)
        return {"agent_id": agent.agent_id, "status": "completed", "text_output": "",
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": None}

    state = {
        "spawn_plan": {"active_agents": [
            {"agent_id": "b-0", "role": "Builder", "model": "claude:sonnet",
             "worktree_path": "/tmp/w", "subtask": "merge the BRANCHNAME work"},
        ]},
        "pending_message": "merge it", "task": "merge it",
        "session_id": "sess-l", "max_turns": 20, "project_path": "/proj",
    }
    with patch("backend.orchestrator.graph._execute_worker", side_effect=fake_execute):
        await run_workers_node(state)  # type: ignore[arg-type]

    assert len(prompts) == 1
    assert "Lessons from past sessions" in prompts[0]
    assert _DRAFT.title in prompts[0]
