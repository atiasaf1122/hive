"""Security HTTP surface.

    GET    /api/security/policies             — load user's custom rules
    PUT    /api/security/policies             — replace the rule set
    GET    /api/security/audit                — query the audit table
    GET    /api/security/audit/export.csv     — same, as CSV
    GET    /api/security/approvals/pending    — in-flight approval requests
    POST   /api/security/approvals/{token}    — approve / reject

The router doesn't know about ApprovalMode — that's a frontend setting
the executor consumes per-call. Persistence of "current mode" lives in
the frontend `useSettings` store.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from backend.persistence.db import DB_PATH
from backend.security import executor as _executor
from backend.security.approval_mode import (
    CustomPolicies,
    load_custom_policies,
    save_custom_policies,
)

router = APIRouter(prefix="/api/security")


class CustomRule(BaseModel):
    pattern: str
    action: str  # "ALLOW" | "CONFIRM" | "BLOCK"


class PoliciesPayload(BaseModel):
    custom_rules: list[CustomRule]


@router.get("/policies")
def get_policies() -> dict:
    pol = load_custom_policies()
    return pol.to_dict()


@router.put("/policies")
def put_policies(body: PoliciesPayload) -> dict:
    pol = CustomPolicies(custom_rules=[r.model_dump() for r in body.custom_rules])
    save_custom_policies(pol)
    return pol.to_dict()


# ── audit ──────────────────────────────────────────────────────────────────

@router.get("/audit")
async def get_audit(
    project_id: str | None = Query(None),
    agent_id: str | None = Query(None),
    classification: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    limit: int = Query(100, ge=1, le=10_000),
    db_path: str | None = Query(None),
) -> dict:
    rows = await _executor.query_audit(
        _executor.AuditQuery(
            project_id=project_id, agent_id=agent_id,
            classification=classification, since=since, until=until,
            limit=limit,
        ),
        db_path=Path(db_path) if db_path else DB_PATH,
    )
    return {"items": [r.__dict__ for r in rows]}


@router.get("/audit/export.csv", response_class=PlainTextResponse)
async def export_audit_csv(
    project_id: str | None = Query(None),
    agent_id: str | None = Query(None),
    classification: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    limit: int = Query(10_000, ge=1, le=100_000),
) -> str:
    rows = await _executor.query_audit(
        _executor.AuditQuery(
            project_id=project_id, agent_id=agent_id,
            classification=classification, since=since, until=until,
            limit=limit,
        ),
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "ts", "project_id", "agent_id", "command", "working_dir",
        "classification", "decision_source", "matched_pattern",
        "exit_code", "duration_ms", "user_approved",
        "stdout_excerpt", "stderr_excerpt",
    ])
    for r in rows:
        writer.writerow([
            r.id, r.ts, r.project_id, r.agent_id, r.command, r.working_dir,
            r.classification, r.decision_source, r.matched_pattern or "",
            r.exit_code if r.exit_code is not None else "",
            r.duration_ms,
            "" if r.user_approved is None else r.user_approved,
            r.stdout_excerpt, r.stderr_excerpt,
        ])
    return buf.getvalue()


# ── approvals ──────────────────────────────────────────────────────────────

@router.get("/approvals/pending")
def list_pending() -> dict:
    return {"items": _executor.list_pending_approvals()}


class ApprovalDecision(BaseModel):
    approved: bool


@router.post("/approvals/{token}")
async def resolve_approval(token: str, body: ApprovalDecision) -> dict:
    try:
        result = await _executor.resume_with_approval(token, body.approved)
    except KeyError:
        raise HTTPException(404, "no such approval token (may have expired)")
    return {
        "status": result.status,
        "audit_id": result.audit_id,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
    }
