"""HTTP surface for the validation stack.

    GET    /api/validation/trust              — all worker trust scores
    GET    /api/validation/trust/{worker_id}  — one
    DELETE /api/validation/trust/{worker_id}  — reset
    POST   /api/validation/cross-check        — run deterministic
                                                validators + (optional)
                                                Haiku semantic cross-check
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.llm.haiku import HaikuBudgetExhausted, build_caller
from backend.validation.schema import CompletionReport
from backend.validation.trust import (
    LOW_TRUST_FLOOR,
    get_trust_score,
    list_trust_scores,
    reset_trust_score,
)
from backend.validation.validators import (
    CommandAuditRow,
    GitFileChange,
    ValidationContext,
    semantic_cross_check,
    validate_report_async,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/validation")

_CROSS_CHECK_BUDGET = int(os.environ.get("HIVE_HAIKU_CROSSCHECK_BUDGET_TOKENS", "20000"))


def _to_dict(s) -> dict:
    return {
        "worker_id": s.worker_id,
        "successful_completions": s.successful_completions,
        "failed_validations": s.failed_validations,
        "total_sessions": s.total_sessions,
        "score": s.score,
        "percentage": s.percentage,
        "last_updated": s.last_updated,
        "low_trust": s.total_sessions >= 10 and s.score < LOW_TRUST_FLOOR,
    }


@router.get("/trust")
async def list_trust() -> dict:
    items = await list_trust_scores()
    return {
        "items": [_to_dict(s) for s in items],
        "low_trust_floor": LOW_TRUST_FLOOR,
    }


@router.get("/trust/{worker_id}")
async def one_trust(worker_id: str) -> dict:
    s = await get_trust_score(worker_id)
    if s is None:
        raise HTTPException(404, "no trust data yet for that worker")
    return _to_dict(s)


@router.delete("/trust/{worker_id}")
async def reset_trust(worker_id: str) -> dict:
    await reset_trust_score(worker_id)
    return {"ok": True, "worker_id": worker_id}


# ── cross-check (deterministic validators + optional Haiku) ─────────────────


class _GitChangeIn(BaseModel):
    path: str
    is_new: bool = False
    is_deleted: bool = False
    lines_added: int = 0
    lines_removed: int = 0


class _AuditRowIn(BaseModel):
    command: str
    exit_code: int | None = None


class CrossCheckBody(BaseModel):
    """Bundle every input the validators + cross-check need.

    The orchestrator passes this when an agent finishes a turn.
    `session_id` is required to (a) scope Haiku budget, (b) record
    the cost row under the right session in the dashboard.
    """
    session_id: str
    report: CompletionReport
    worktree_path: str = ""
    git_changes: list[_GitChangeIn] = Field(default_factory=list)
    audit_rows: list[_AuditRowIn] = Field(default_factory=list)
    installed_packages_after: list[str] = Field(default_factory=list)
    run_semantic_check: bool = True


@router.post("/cross-check")
async def cross_check(body: CrossCheckBody) -> dict:
    """Run every deterministic validator, then optionally call Haiku."""
    ctx = ValidationContext(
        worktree_path=body.worktree_path,
        git_changes=[
            GitFileChange(
                path=c.path, is_new=c.is_new, is_deleted=c.is_deleted,
                lines_added=c.lines_added, lines_removed=c.lines_removed,
            ) for c in body.git_changes
        ],
        audit_rows=[
            CommandAuditRow(command=a.command, exit_code=a.exit_code)
            for a in body.audit_rows
        ],
        installed_packages_after=set(body.installed_packages_after),
    )

    det = await validate_report_async(body.report, ctx)

    semantic_payload: dict | None = None
    if body.run_semantic_check:
        caller = build_caller(body.session_id, budget_tokens=_CROSS_CHECK_BUDGET)
        try:
            sem = await semantic_cross_check(body.report, ctx, haiku_caller=caller)
        except HaikuBudgetExhausted as exc:
            logger.info(
                "Cross-check budget exhausted for session %s: %s",
                body.session_id, exc,
            )
            sem = None
            semantic_payload = {
                "skipped": True, "skipped_reason": "budget_exhausted",
                "score": 0.0, "rationale": str(exc),
            }
        if sem is not None:
            semantic_payload = {
                "score": sem.score, "rationale": sem.rationale,
                "skipped": sem.skipped, "skipped_reason": sem.skipped_reason,
            }

    return {
        "deterministic": {
            "passed": det.passed,
            "has_critical_issues": det.has_critical_issues,
            "findings": [
                {"validator": f.validator, "ok": f.ok,
                 "detail": f.detail, "severity": f.severity}
                for f in det.findings
            ],
        },
        "semantic": semantic_payload,
    }
