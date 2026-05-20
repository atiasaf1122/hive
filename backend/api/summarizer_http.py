"""Summarizer HTTP — tiered worker-turn rollup with optional verification.

    POST /api/summarizer/run

The body carries an event transcript (rendered or raw events) plus
the session_id used for Haiku budget scoping. The response is a
tiered summary; if `verify=true` we additionally feed the detailed
report through the deterministic validator stack and report whether
it passes (the "verification-before-VRAM-release" gate).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.llm.haiku import HaikuBudgetExhausted, build_caller
from backend.summarizer.runner import (
    SummarizerError,
    summarize_transcript,
)
from backend.validation.validators import (
    CommandAuditRow,
    GitFileChange,
    ValidationContext,
    validate_report_async,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/summarizer")

# Summariser does most of the heavy lifting; give it more headroom than rerank.
_SUMMARIZER_BUDGET = int(
    os.environ.get("HIVE_HAIKU_SUMMARIZER_BUDGET_TOKENS", "30000")
)


class _GitChangeIn(BaseModel):
    path: str
    is_new: bool = False
    is_deleted: bool = False
    lines_added: int = 0
    lines_removed: int = 0


class _AuditRowIn(BaseModel):
    command: str
    exit_code: int | None = None


class SummarizerRequest(BaseModel):
    session_id: str
    transcript: str
    task_description: str = ""
    verify: bool = False
    worktree_path: str = ""
    # Optional context for the deterministic verifier — only consulted
    # when `verify=true`. Pass what the orchestrator collected from the
    # worktree right after the worker finished.
    git_changes: list[_GitChangeIn] = Field(default_factory=list)
    audit_rows: list[_AuditRowIn] = Field(default_factory=list)
    installed_packages_after: list[str] = Field(default_factory=list)


@router.post("/run")
async def run_summarizer(body: SummarizerRequest) -> dict:
    if not body.transcript.strip():
        raise HTTPException(400, "transcript is required")

    caller = build_caller(body.session_id, budget_tokens=_SUMMARIZER_BUDGET)
    try:
        summary = await summarize_transcript(
            body.transcript,
            haiku_caller=caller,
            task_description=body.task_description,
        )
    except HaikuBudgetExhausted as exc:
        logger.info("Summarizer budget exhausted for %s", body.session_id)
        raise HTTPException(429, f"haiku budget exhausted: {exc}") from exc
    except SummarizerError as exc:
        raise HTTPException(502, f"summariser could not parse Haiku: {exc}") from exc

    payload: dict[str, Any] = {
        "tldr": summary.tldr,
        "standard": summary.standard,
        "detailed": summary.detailed.model_dump() if summary.detailed else None,
    }

    if body.verify and summary.detailed is not None:
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
        result = await validate_report_async(summary.detailed, ctx)
        payload["verification"] = {
            "passed": result.passed,
            "has_critical_issues": result.has_critical_issues,
            "findings": [
                {"validator": f.validator, "ok": f.ok,
                 "detail": f.detail, "severity": f.severity}
                for f in result.findings
            ],
        }
    return payload
