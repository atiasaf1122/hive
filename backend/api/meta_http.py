"""META HTTP surface (D8).

    POST /api/meta/run            {project_path?} → report + saved path
    POST /api/meta/accept-lesson  a [lesson to add] recommendation — passes
                                  through the SAME D1.3 groundedness gate as
                                  every other lesson; META is not exempt.

Nothing here executes recommendations. META advises; the user decides.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/meta")


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
    distiller = _default_distiller("meta-accept")
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
