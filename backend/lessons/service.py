"""Session-close lesson pipeline: triggers → distill → gate → save (D1.2/D1.3/D1.5).

Triggers are collected from PERSISTED events only. The supported grounded
triggers:
  1. a validation failure for a logical agent followed by a later clean run
     of the same agent in the session (fix observable),
  2. an llm_review intervention (its resolution reasoning is evidence),
  3. an infrastructure failure event with a concrete recorded cause.
User-manual-correction detection (worktree diff between turns) is NOT
implemented — worktrees are removed after each merge, so detection would be
unreliable; skipped per spec.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from backend.persistence.db import DB_PATH
from backend.persistence.events import get_session_events, write_event
from backend.workers.base import EventType, HiveEvent

logger = logging.getLogger(__name__)

MAX_TRIGGERS_PER_SESSION = 5   # bound the distillation cost
DUPLICATE_THRESHOLD = 0.80     # skip drafts too similar to an existing lesson


@dataclass
class Trigger:
    kind: str        # 'validation_fixed' | 'llm_review' | 'infrastructure'
    origin: str      # 'agent' | 'infrastructure'
    evidence: str


def _payload(ev: dict) -> dict:
    """Persisted event payloads nest orchestrator data under raw_payload;
    merge it flat so triggers read one shape."""
    payload = ev.get("payload") or {}
    return {**payload, **(payload.get("raw_payload") or {})}


def _collect_triggers(events: list[dict]) -> list[Trigger]:
    triggers: list[Trigger] = []

    # 1. validation failure → later clean run of the same agent.
    failures: dict[str, list[tuple[int, str]]] = {}
    for i, ev in enumerate(events):
        if ev["type"] == str(EventType.VALIDATION_FAILED):
            findings = _payload(ev).get("findings") or []
            failures.setdefault(ev["agent_id"], []).append((i, "; ".join(findings)))
    for agent_id, fails in failures.items():
        last_fail_idx, findings = fails[-1]
        later = events[last_fail_idx + 1:]
        later_clean_end = any(
            e["agent_id"] == agent_id and e["type"] == str(EventType.AGENT_END)
            for e in later
        ) and not any(
            e["agent_id"] == agent_id and e["type"] == str(EventType.VALIDATION_FAILED)
            for e in later
        )
        if later_clean_end and findings.strip():
            triggers.append(Trigger(
                kind="validation_fixed", origin="agent",
                evidence=(
                    f"Validator diagnosis for agent {agent_id}: {findings}\n"
                    f"A later run of the same agent in this session completed "
                    f"with no validation failure (the fix is observable)."
                ),
            ))

    # 2. llm_review interventions.
    for ev in events:
        if ev["type"] == str(EventType.REVIEW_LLM):
            notes = _payload(ev).get("notes") or []
            text = "\n".join(notes).strip()
            if text:
                triggers.append(Trigger(
                    kind="llm_review", origin="agent",
                    evidence=f"Reviewer resolution reasoning:\n{text}",
                ))

    # 3. infrastructure failures with a concrete recorded cause.
    seen_errors: set[str] = set()
    for ev in events:
        if ev["type"] == str(EventType.AGENT_ERROR):
            if _payload(ev).get("origin") != "infrastructure":
                continue
            error = (_payload(ev).get("error") or "").strip()
            if not error or error in seen_errors:
                continue
            seen_errors.add(error)
            triggers.append(Trigger(
                kind="infrastructure", origin="infrastructure",
                evidence=f"Infrastructure failure (agent {ev['agent_id']}): {error}",
            ))

    return triggers[:MAX_TRIGGERS_PER_SESSION]


async def _default_distiller(session_id: str):
    # E4: distillation runs on a local model when one is available (free
    # on the user's GPUs), Haiku otherwise — selection + fallback live in
    # internal_task_caller. The distiller class is caller-agnostic.
    from backend.lessons.distiller import HaikuLessonDistiller
    from backend.llm.local import internal_task_caller

    caller, label = await internal_task_caller(
        "distillation", session_id, agent_id_prefix="lesson-distiller")
    logger.info("Distiller engine for %s: %s", session_id, label)
    return HaikuLessonDistiller(caller)


async def _is_duplicate(draft, db_path: Path) -> bool:
    from backend.lessons.store import list_lessons
    from backend.skills.embedder import cosine_similarity, embed

    existing = await list_lessons(status="active", db_path=db_path)
    if not existing:
        return False
    draft_vec = embed(f"{draft.trigger_context}\n{draft.content}")
    for lesson in existing:
        vec = embed(f"{lesson.trigger_context}\n{lesson.content}")
        if cosine_similarity(draft_vec, vec) >= DUPLICATE_THRESHOLD:
            return True
    return False


async def distill_session_lessons(
    session_id: str,
    project_path: str | None,
    distiller=None,
    db_path: Path = DB_PATH,
) -> list[int]:
    """Run at session close. Returns ids of lessons saved. Never raises."""
    saved: list[int] = []
    try:
        events = await get_session_events(session_id, path=db_path)
        triggers = _collect_triggers(events)
        if not triggers:
            return []
        distiller = distiller or await _default_distiller(session_id)

        # E0.1 audit-trail invariant: each trigger emits EXACTLY ONE of
        # LESSON_STORED / LESSON_DISCARDED / LESSON_NONE. The Phase D e2e
        # stored nothing AND logged nothing, and "legitimately NONE" vs
        # "silently dropped" could not be distinguished after the fact.
        for trigger in triggers:
            try:
                await _distill_one(
                    trigger, distiller, session_id, project_path, db_path, saved)
            except Exception as exc:  # noqa: BLE001 — one bad trigger must not
                # silence the others, and even the error leaves a trail.
                logger.warning("Distillation attempt failed for %s: %s", session_id, exc)
                await write_event(HiveEvent(
                    type=EventType.LESSON_NONE,
                    agent_id="lesson-distiller", session_id=session_id,
                    raw_payload={"reason": f"distiller error: {exc}",
                                 "trigger_kind": trigger.kind},
                ), path=db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Lesson distillation failed for %s: %s", session_id, exc)
    return saved


async def _distill_one(
    trigger: Trigger, distiller, session_id: str,
    project_path: str | None, db_path: Path, saved: list[int],
) -> None:
    from backend.lessons.distiller import GATE_THRESHOLD
    from backend.lessons.store import save_lesson

    result = await distiller.distill(trigger.evidence, origin=trigger.origin)
    if result.draft is None:
        await write_event(HiveEvent(
            type=EventType.LESSON_NONE,
            agent_id="lesson-distiller", session_id=session_id,
            raw_payload={"reason": result.reason, "trigger_kind": trigger.kind,
                         "evidence": trigger.evidence[:1000]},
        ), path=db_path)
        logger.info("Lesson NONE (%s): %s", trigger.kind, result.reason)
        return
    draft = result.draft
    gate = await distiller.gate(draft, trigger.evidence)
    if gate.score < GATE_THRESHOLD:
        await write_event(HiveEvent(
            type=EventType.LESSON_DISCARDED,
            agent_id="lesson-distiller", session_id=session_id,
            raw_payload={"gate_score": gate.score, "draft_title": draft.title,
                         "reason": gate.reason or "below groundedness gate",
                         "content": draft.content,
                         "evidence": trigger.evidence[:1000]},
        ), path=db_path)
        logger.info("Lesson discarded by gate (score %d): %s", gate.score, draft.title)
        return
    if await _is_duplicate(draft, db_path):
        await write_event(HiveEvent(
            type=EventType.LESSON_DISCARDED,
            agent_id="lesson-distiller", session_id=session_id,
            raw_payload={"gate_score": gate.score, "draft_title": draft.title,
                         "reason": "near-duplicate of an existing active lesson"},
        ), path=db_path)
        logger.info("Lesson skipped as duplicate: %s", draft.title)
        return
    lesson_id = await save_lesson(
        scope="project" if project_path else "global",
        project_path=project_path,
        title=draft.title, description=draft.description,
        content=draft.content, trigger_context=draft.trigger_context,
        origin=draft.origin, source_session=session_id,
        source_evidence=trigger.evidence[:2000],
        db_path=db_path,
    )
    saved.append(lesson_id)
    await write_event(HiveEvent(
        type=EventType.LESSON_STORED,
        agent_id="lesson-distiller", session_id=session_id,
        raw_payload={"lesson_id": lesson_id, "gate_score": gate.score,
                     "title": draft.title},
    ), path=db_path)
    logger.info("Lesson %d saved (gate %d): %s", lesson_id, gate.score, draft.title)


async def run_session_hygiene(session_id: str, db_path: Path = DB_PATH) -> dict[int, bool]:
    """Close the confirm/archive loop for lessons injected this session."""
    from backend.lessons.store import resolve_applications

    try:
        events = await get_session_events(session_id, path=db_path)
        failure_texts = []
        for ev in events:
            if ev["type"] == str(EventType.VALIDATION_FAILED):
                failure_texts.append("; ".join(_payload(ev).get("findings") or []))
            elif ev["type"] == str(EventType.AGENT_ERROR):
                failure_texts.append(_payload(ev).get("error") or "")
        return await resolve_applications(session_id, failure_texts, db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Lesson hygiene failed for %s: %s", session_id, exc)
        return {}
