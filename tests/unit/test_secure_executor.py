"""Secure executor + audit log + approval flow."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from backend.main import app
from backend.persistence.db import get_conn, init_db
from backend.security import executor as _executor
from backend.security.approval_mode import ApprovalMode
from backend.security.command_policy import CommandClassification
from backend.security.executor import (
    AuditQuery,
    ExecuteResult,
    purge_old_audit_rows,
    query_audit,
    resume_with_approval,
    secure_execute,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    await init_db(p)
    return p


@pytest.fixture(autouse=True)
def _reset_pending():
    _executor._pending.clear()


# ── allowed commands run immediately + are audited ──────────────────────────

@pytest.mark.asyncio
async def test_allowed_command_runs_and_audits(db: Path) -> None:
    # `echo` isn't in the policy list — but `git status` is. We use `git
    # config --get user.name` to avoid needing a real repo.
    result = await secure_execute(
        "git --version",
        mode=ApprovalMode.SMART_AUTO,
        working_dir=str(db.parent),
        agent_id="agent-a",
        project_id="proj-1",
        db_path=db,
    )
    assert result.status == "completed"
    assert result.decision.classification is CommandClassification.ALLOWED
    assert result.exit_code == 0
    assert result.audit_id and result.audit_id > 0
    assert "git" in result.stdout.lower()

    rows = await query_audit(AuditQuery(project_id="proj-1"), db_path=db)
    assert len(rows) == 1
    assert rows[0].classification == "allowed"


# ── blocked commands never execute but ARE audited ──────────────────────────

@pytest.mark.asyncio
async def test_blocked_command_does_not_execute(db: Path, tmp_path: Path) -> None:
    # If this actually ran it would try to wipe the filesystem. We trust
    # the policy to catch it; the test just verifies the audit row.
    canary = tmp_path / "canary.txt"
    canary.write_text("safe")

    result = await secure_execute(
        "rm -rf /",
        mode=ApprovalMode.BLIND_AUTO,  # even the loosest mode must block this
        working_dir=str(tmp_path),
        db_path=db,
    )
    assert result.status == "blocked"
    assert result.decision.classification is CommandClassification.BLOCKED
    assert canary.exists()  # we did not delete anything

    rows = await query_audit(AuditQuery(), db_path=db)
    assert any(r.classification == "blocked" for r in rows)


# ── confirmation path returns a token; resume executes ──────────────────────

@pytest.mark.asyncio
async def test_confirmation_pending_then_approved(db: Path, tmp_path: Path) -> None:
    result = await secure_execute(
        "npm install left-pad",
        mode=ApprovalMode.SMART_AUTO,
        working_dir=str(tmp_path),
        db_path=db,
    )
    assert result.status == "pending_approval"
    assert result.pending_token

    # Replace the heavy npm command with `true` so the resume executes
    # something safe. We re-route via the same token.
    pending = _executor._pending[result.pending_token]
    pending.cmd = "true"  # POSIX success

    resumed = await resume_with_approval(result.pending_token, approved=True, db_path=db)
    assert resumed.status == "completed"
    assert resumed.exit_code == 0
    rows = await query_audit(AuditQuery(), db_path=db)
    # One row for the resumed execution; the original ask doesn't audit
    # until decision time.
    assert len(rows) == 1
    assert rows[0].classification == "confirmed"
    assert rows[0].user_approved == 1


@pytest.mark.asyncio
async def test_confirmation_pending_then_rejected(db: Path, tmp_path: Path) -> None:
    result = await secure_execute(
        "npm install left-pad",
        mode=ApprovalMode.SMART_AUTO,
        working_dir=str(tmp_path),
        db_path=db,
    )
    assert result.status == "pending_approval"

    resumed = await resume_with_approval(result.pending_token, approved=False, db_path=db)
    assert resumed.status == "blocked"
    rows = await query_audit(AuditQuery(), db_path=db)
    assert any(r.user_approved == 0 for r in rows)


@pytest.mark.asyncio
async def test_unknown_token_raises(db: Path) -> None:
    with pytest.raises(KeyError):
        await resume_with_approval("nope", approved=True, db_path=db)


# ── full_auto runs the confirmation commands without asking ─────────────────

@pytest.mark.asyncio
async def test_full_auto_runs_confirmation_commands(db: Path, tmp_path: Path) -> None:
    # `npm install` would hit the network; substitute `true` while keeping the
    # classification by passing a custom rule.
    rules = [{"pattern": r"^true$", "action": "CONFIRM"}]
    result = await secure_execute(
        "true",
        mode=ApprovalMode.FULL_AUTO,
        working_dir=str(tmp_path),
        custom_rules=rules,
        db_path=db,
    )
    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.decision.rule_source == "custom"
    rows = await query_audit(AuditQuery(), db_path=db)
    assert rows[0].classification == "confirmed"


# ── audit retention ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_purge_old_rows_drops_only_old_ones(db: Path) -> None:
    # Insert one row from "today" and one from 60 days ago.
    async with get_conn(db) as conn:
        await conn.execute(
            "INSERT INTO command_audit (project_id, agent_id, command, classification) "
            "VALUES (?, ?, ?, ?)",
            ("p", "a", "true", "allowed"),
        )
        await conn.execute(
            "INSERT INTO command_audit (ts, project_id, agent_id, command, classification) "
            "VALUES (datetime('now', '-60 days'), ?, ?, ?, ?)",
            ("p", "a", "rm anything", "blocked"),
        )
        await conn.commit()

    deleted = await purge_old_audit_rows(30, db_path=db)
    assert deleted == 1

    rows = await query_audit(AuditQuery(), db_path=db)
    assert len(rows) == 1
    assert rows[0].classification == "allowed"


# ── HTTP integration ────────────────────────────────────────────────────────

def test_policies_round_trip_via_http(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "policies.json"
    from backend.security import approval_mode as am
    monkeypatch.setattr(am, "CUSTOM_POLICIES_FILE", target)

    with TestClient(app) as client:
        # Initially empty.
        resp = client.get("/api/security/policies")
        assert resp.status_code == 200
        assert resp.json() == {"custom_rules": []}

        body = {"custom_rules": [
            {"pattern": r"^docker", "action": "BLOCK"},
            {"pattern": r"^git push", "action": "CONFIRM"},
        ]}
        resp = client.put("/api/security/policies", json=body)
        assert resp.status_code == 200

        loaded = am.load_custom_policies(target)
        assert len(loaded.custom_rules) == 2


def test_audit_query_endpoint() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/security/audit?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body


def test_audit_csv_export_returns_csv() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/security/audit/export.csv?limit=5")
    assert resp.status_code == 200
    # First line is the CSV header.
    assert "ts,project_id" in resp.text


def test_pending_approvals_endpoint() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/security/approvals/pending")
    assert resp.status_code == 200
    assert "items" in resp.json()


def test_resolve_approval_404_for_unknown_token() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/security/approvals/nope-not-a-token",
            json={"approved": True},
        )
    assert resp.status_code == 404


# pacify unused-import linters when run standalone
_ = (asyncio, ExecuteResult)
