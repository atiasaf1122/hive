"""META HTTP surface (D8 + F0.3).

    POST /api/meta/run            {project_path?} → report + saved path
    POST /api/meta/accept-lesson  a [lesson to add] recommendation — passes
                                  through the SAME D1.3 groundedness gate as
                                  every other lesson; META is not exempt.
    GET  /api/meta/nudge          F0.3: ≥3 same-class failures in 24h →
                                  "Recurring failures — run META?" badge.
                                  Replaces the never-wired pattern_detector
                                  (its ActivityWindow shape fit live-session
                                  stalls, not cross-session clustering — this
                                  is the 40-line query it would have wrapped).
                                  No schedule, no auto-run; a nudge only.

Nothing here executes recommendations. META advises; the user decides.
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/meta")

# Failure-shaped event types worth clustering. guard/tripped is F1's event —
# listed now so guard denials cluster the moment they exist.
_FAILURE_TYPES = ("agent/error", "validation/failed", "guard/tripped")
_NUDGE_THRESHOLD = 3
_NUDGE_WINDOW_HOURS = 24
# Strip volatile fragments so "exited 1 (pid 123)" and "(pid 456)" cluster.
_VOLATILE_RE = re.compile(r"[0-9a-f]{6,}|\d+|/[^\s\"']+")


def _failure_class(event_type: str, payload: dict) -> str:
    merged = {**payload, **(payload.get("raw_payload") or {})}
    if event_type == "validation/failed":
        text = "; ".join(merged.get("findings") or [])
    else:
        text = str(merged.get("error") or merged.get("reason") or "")
    return f"{event_type}: {_VOLATILE_RE.sub('*', text)[:80].strip()}"


@router.get("/nudge")
async def meta_nudge() -> dict:
    from backend.persistence.db import DB_PATH, get_conn

    async with get_conn(DB_PATH) as conn:
        cur = await conn.execute(
            "SELECT type, payload_json FROM events "
            "WHERE type IN (?,?,?) AND ts >= (strftime('%s','now') - ? * 3600)",
            (*_FAILURE_TYPES, _NUDGE_WINDOW_HOURS),
        )
        rows = await cur.fetchall()

    counts: dict[str, int] = {}
    for r in rows:
        try:
            payload = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            payload = {}
        key = _failure_class(r["type"], payload)
        counts[key] = counts.get(key, 0) + 1

    clusters = sorted(
        ({"failure_class": k, "count": n} for k, n in counts.items()
         if n >= _NUDGE_THRESHOLD),
        key=lambda c: -c["count"],
    )
    return {
        "should_nudge": bool(clusters),
        "window_hours": _NUDGE_WINDOW_HOURS,
        "threshold": _NUDGE_THRESHOLD,
        "clusters": clusters,
    }


class MetaRunRequest(BaseModel):
    project_path: str | None = None


@router.post("/run")
async def meta_run(req: MetaRunRequest) -> dict:
    from backend.meta.analyzer import run_meta

    try:
        report, path = await run_meta(req.project_path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"META failed: {exc}") from exc
    return {"ok": True, "report_path": str(path), "report": report}


class AcceptLessonRequest(BaseModel):
    title: str
    description: str
    content: str
    trigger_context: str
    evidence: str                     # what META grounded the suggestion in
    project_path: str | None = None


@router.post("/accept-lesson")
async def accept_lesson(req: AcceptLessonRequest) -> dict:
    from backend.lessons.distiller import GATE_THRESHOLD, LessonDraft
    from backend.lessons.service import _default_distiller
    from backend.lessons.store import save_lesson

    draft = LessonDraft(
        title=req.title, description=req.description, content=req.content,
        trigger_context=req.trigger_context, origin="agent",
    )
    distiller = await _default_distiller("meta-accept")
    score = (await distiller.gate(draft, req.evidence)).score
    if score < GATE_THRESHOLD:
        raise HTTPException(
            status_code=422,
            detail=f"Rejected by the groundedness gate (score {score}/10 — "
                   f"META suggestions are not exempt from evidence).",
        )
    lesson_id = await save_lesson(
        scope="project" if req.project_path else "global",
        project_path=req.project_path,
        title=req.title, description=req.description, content=req.content,
        trigger_context=req.trigger_context, origin="agent",
        source_session="meta", source_evidence=req.evidence[:2000],
    )
    return {"ok": True, "lesson_id": lesson_id, "gate_score": score}
