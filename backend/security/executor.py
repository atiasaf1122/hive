"""Secure subprocess executor with audit logging.

`secure_execute()` is the single chokepoint other code is meant to call
when an agent wants to run a shell command. It:

  1. Classifies the command through `command_policy.classify_command`,
     layering in the user's custom rules.
  2. Decides what to do based on the active `ApprovalMode`:
       run / ask / block.
  3. If 'ask': returns immediately with a `pending_approval` token —
     the caller wires it up to an `command_approval_requested` WS event
     and resumes via `resume_with_approval(token, approved=True|False)`.
  4. If 'run' (or post-approval): executes via `asyncio.subprocess`,
     captures stdout + stderr (truncated to 500 chars each in the
     audit row — full streams still go to the agent log via WS).
  5. Writes a row into `command_audit` no matter what.

The audit row is the only durable signal of what happened. Even
'blocked' commands get logged so users can see what the agent *tried*
to do.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from backend.persistence.db import DB_PATH, get_conn
from backend.security.approval_mode import (
    ApprovalMode,
    evaluate,
    load_custom_policies,
)
from backend.security.command_policy import CommandClassification, Decision

logger = logging.getLogger(__name__)

EXCERPT_MAX = 500


@dataclass
class ExecuteResult:
    """Outcome surfaced back to the caller."""
    status: str                 # 'completed' | 'blocked' | 'pending_approval'
    decision: Decision
    audit_id: int | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    pending_token: str | None = None  # only set when status='pending_approval'


@dataclass
class PendingApproval:
    """In-flight approval — kept in memory until the user decides."""
    token: str
    cmd: str
    decision: Decision
    working_dir: str
    project_id: str
    agent_id: str
    mode: ApprovalMode
    timeout_at: float          # epoch seconds — request expires after a while
    future: asyncio.Future     # resolved by resume_with_approval


_pending: dict[str, PendingApproval] = {}


async def secure_execute(
    cmd: str,
    *,
    mode: ApprovalMode = ApprovalMode.SMART_AUTO,
    working_dir: str | Path = ".",
    agent_id: str = "",
    project_id: str = "",
    timeout_seconds: float | None = None,
    custom_rules: list[dict] | None = None,
    db_path: Path = DB_PATH,
) -> ExecuteResult:
    """Run `cmd` through the policy gate. See module docstring for the flow."""
    if custom_rules is None:
        custom_rules = load_custom_policies().custom_rules

    decision, action = evaluate(cmd, mode, custom_rules=custom_rules)

    if action == "block":
        audit_id = await _audit(
            cmd=cmd, classification="blocked",
            decision=decision, working_dir=str(working_dir),
            agent_id=agent_id, project_id=project_id,
            exit_code=None, stdout="", stderr="",
            duration_ms=0, user_approved=None, db_path=db_path,
        )
        logger.warning("Blocked command for agent=%s: %s (%s)",
                       agent_id, cmd, decision.rationale)
        return ExecuteResult(
            status="blocked", decision=decision, audit_id=audit_id,
        )

    if action == "ask":
        token = uuid.uuid4().hex[:12]
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        _pending[token] = PendingApproval(
            token=token, cmd=cmd, decision=decision,
            working_dir=str(working_dir), project_id=project_id,
            agent_id=agent_id, mode=mode,
            timeout_at=time.time() + 600,  # 10-minute approval window
            future=future,
        )
        return ExecuteResult(
            status="pending_approval", decision=decision,
            pending_token=token,
        )

    # action == "run"
    return await _run_and_audit(
        cmd=cmd, decision=decision, working_dir=str(working_dir),
        agent_id=agent_id, project_id=project_id,
        timeout_seconds=timeout_seconds, user_approved=None,
        db_path=db_path,
    )


async def resume_with_approval(
    token: str,
    approved: bool,
    *,
    timeout_seconds: float | None = None,
    db_path: Path = DB_PATH,
) -> ExecuteResult:
    """Resolve a `pending_approval` from `secure_execute()`."""
    pending = _pending.pop(token, None)
    if pending is None:
        raise KeyError(f"unknown or expired approval token: {token}")

    if not pending.future.done():
        pending.future.set_result(approved)

    if not approved:
        audit_id = await _audit(
            cmd=pending.cmd, classification="confirmed",
            decision=pending.decision, working_dir=pending.working_dir,
            agent_id=pending.agent_id, project_id=pending.project_id,
            exit_code=None, stdout="", stderr="", duration_ms=0,
            user_approved=False, db_path=db_path,
        )
        return ExecuteResult(
            status="blocked", decision=pending.decision, audit_id=audit_id,
        )

    return await _run_and_audit(
        cmd=pending.cmd, decision=pending.decision,
        working_dir=pending.working_dir, agent_id=pending.agent_id,
        project_id=pending.project_id, timeout_seconds=timeout_seconds,
        user_approved=True, db_path=db_path,
    )


def list_pending_approvals() -> list[dict]:
    """Snapshot of in-flight approvals (for the API to surface)."""
    now = time.time()
    # Purge stale ones first.
    for token in list(_pending):
        if _pending[token].timeout_at < now:
            _pending.pop(token, None)
    return [
        {
            "token": p.token,
            "command": p.cmd,
            "agent_id": p.agent_id,
            "project_id": p.project_id,
            "matched_pattern": p.decision.matched_pattern,
            "rationale": p.decision.rationale,
            "rule_source": p.decision.rule_source,
            "expires_at": p.timeout_at,
        }
        for p in _pending.values()
    ]


# ── internals ───────────────────────────────────────────────────────────────


async def _run_and_audit(
    *,
    cmd: str,
    decision: Decision,
    working_dir: str,
    agent_id: str,
    project_id: str,
    timeout_seconds: float | None,
    user_approved: bool | None,
    db_path: Path,
) -> ExecuteResult:
    """Spawn the subprocess, capture output, write the audit row."""
    start = time.time()
    stdout = ""
    stderr = ""
    exit_code: int | None = None

    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=working_dir or None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            stderr = f"[timed out after {timeout_seconds}s]"
            exit_code = -1
        else:
            stdout = out.decode("utf-8", errors="replace")
            stderr = err.decode("utf-8", errors="replace")
            exit_code = proc.returncode
    except (FileNotFoundError, PermissionError, OSError) as exc:
        stderr = f"[spawn failed: {exc}]"
        exit_code = -1

    duration_ms = int((time.time() - start) * 1000)
    # Audit classification reflects the *policy bucket* the command landed
    # in, not whether the user was prompted. A REQUIRES_CONFIRMATION command
    # auto-run in FULL_AUTO is still recorded as `confirmed`; the
    # `user_approved` column carries the prompt detail.
    if decision.classification is CommandClassification.CONFIRMATION:
        classification = "confirmed"
    elif decision.classification is CommandClassification.ALLOWED:
        classification = "allowed"
    else:
        classification = "blocked"

    audit_id = await _audit(
        cmd=cmd, classification=classification, decision=decision,
        working_dir=working_dir, agent_id=agent_id, project_id=project_id,
        exit_code=exit_code, stdout=stdout, stderr=stderr,
        duration_ms=duration_ms, user_approved=user_approved,
        db_path=db_path,
    )

    return ExecuteResult(
        status="completed", decision=decision, audit_id=audit_id,
        exit_code=exit_code, stdout=stdout, stderr=stderr,
        duration_ms=duration_ms,
    )


async def _audit(
    *,
    cmd: str,
    classification: str,
    decision: Decision,
    working_dir: str,
    agent_id: str,
    project_id: str,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    duration_ms: int,
    user_approved: bool | None,
    db_path: Path,
) -> int:
    """Insert a row into command_audit. Returns the row id."""
    async with get_conn(db_path) as conn:
        cur = await conn.execute(
            """
            INSERT INTO command_audit (
                project_id, agent_id, command, working_dir,
                classification, decision_source, matched_pattern,
                exit_code, stdout_excerpt, stderr_excerpt,
                duration_ms, user_approved
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id, agent_id, cmd, working_dir,
                classification, decision.rule_source, decision.matched_pattern,
                exit_code,
                (stdout or "")[:EXCERPT_MAX],
                (stderr or "")[:EXCERPT_MAX],
                duration_ms,
                None if user_approved is None else (1 if user_approved else 0),
            ),
        )
        await conn.commit()
        return cur.lastrowid or 0


@dataclass
class AuditQuery:
    project_id: str | None = None
    agent_id: str | None = None
    classification: str | None = None
    since: str | None = None     # ISO-8601 datetime string
    until: str | None = None
    limit: int = 100


@dataclass
class AuditRow:
    id: int
    ts: str
    project_id: str
    agent_id: str
    command: str
    working_dir: str
    classification: str
    decision_source: str
    matched_pattern: str | None
    exit_code: int | None
    stdout_excerpt: str
    stderr_excerpt: str
    duration_ms: int
    user_approved: int | None


async def query_audit(q: AuditQuery, db_path: Path = DB_PATH) -> list[AuditRow]:
    """Read rows back. Used by the audit viewer and CSV export."""
    where: list[str] = []
    params: list = []
    if q.project_id:
        where.append("project_id = ?")
        params.append(q.project_id)
    if q.agent_id:
        where.append("agent_id = ?")
        params.append(q.agent_id)
    if q.classification:
        where.append("classification = ?")
        params.append(q.classification)
    if q.since:
        where.append("ts >= ?")
        params.append(q.since)
    if q.until:
        where.append("ts <= ?")
        params.append(q.until)

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(int(q.limit))

    async with get_conn(db_path) as conn:
        cur = await conn.execute(
            f"SELECT * FROM command_audit {clause} ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
        rows = await cur.fetchall()

    return [AuditRow(**dict(r)) for r in rows]


async def purge_old_audit_rows(retention_days: int, db_path: Path = DB_PATH) -> int:
    """Delete rows older than the configured retention window."""
    days = max(1, int(retention_days))
    async with get_conn(db_path) as conn:
        cur = await conn.execute(
            "DELETE FROM command_audit WHERE ts < datetime('now', ?)",
            (f"-{days} days",),
        )
        await conn.commit()
        return cur.rowcount or 0
