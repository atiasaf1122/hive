"""Validation stack — evidence schema, validators, trust scores."""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from backend.main import app
from backend.persistence.db import init_db
from backend.validation.schema import (
    CompletionReport, Evidence, FileTouched, TestRun,
)
from backend.validation.trust import (
    LOW_TRUST_FLOOR, get_trust_score, is_low_trust, list_trust_scores,
    record_completion, reset_trust_score,
)
from backend.validation.validators import (
    CommandAuditRow, FileCreationValidator, FileDeletionValidator,
    FileModificationValidator, GitFileChange, PackageInstallValidator,
    TestRunValidator, ValidationContext, semantic_cross_check,
    validate_report, validate_report_async,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    await init_db(p)
    return p


# ── FileModificationValidator ──────────────────────────────────────────────

def test_creation_claim_passes_when_git_shows_new() -> None:
    report = CompletionReport(
        status="done",
        evidence=Evidence(files_touched=[FileTouched(
            path="auth.ts", action="created", lines_added=89,
        )]),
    )
    ctx = ValidationContext(git_changes=[
        GitFileChange(path="auth.ts", is_new=True, is_deleted=False,
                      lines_added=89, lines_removed=0),
    ])
    findings = FileModificationValidator().validate(report, ctx)
    assert findings == []


def test_creation_claim_fails_when_no_git_change() -> None:
    report = CompletionReport(
        status="done",
        evidence=Evidence(files_touched=[FileTouched(
            path="auth.ts", action="created",
        )]),
    )
    findings = FileModificationValidator().validate(report, ValidationContext())
    assert len(findings) == 1
    assert not findings[0].ok


def test_modification_with_zero_diff_is_warning_not_error() -> None:
    report = CompletionReport(
        status="done",
        evidence=Evidence(files_touched=[FileTouched(
            path="foo.py", action="modified", lines_added=10,
        )]),
    )
    ctx = ValidationContext(git_changes=[
        GitFileChange(path="foo.py", is_new=False, is_deleted=False),
    ])
    findings = FileModificationValidator().validate(report, ctx)
    assert len(findings) == 1
    assert not findings[0].ok
    assert findings[0].severity == "warning"


def test_deletion_claim_validated_against_git_status() -> None:
    report = CompletionReport(
        status="done",
        evidence=Evidence(files_touched=[FileTouched(
            path="old.py", action="deleted",
        )]),
    )
    ok_ctx = ValidationContext(git_changes=[
        GitFileChange(path="old.py", is_new=False, is_deleted=True),
    ])
    bad_ctx = ValidationContext(git_changes=[
        GitFileChange(path="old.py", is_new=False, is_deleted=False),
    ])
    assert FileModificationValidator().validate(report, ok_ctx) == []
    assert FileModificationValidator().validate(report, bad_ctx)


# ── FileCreationValidator (filesystem-touching) ─────────────────────────────

def test_creation_validator_checks_real_filesystem(tmp_path: Path) -> None:
    real = tmp_path / "src" / "a.py"
    real.parent.mkdir(parents=True)
    real.write_text("x = 1")

    report = CompletionReport(
        status="done",
        evidence=Evidence(files_touched=[
            FileTouched(path="src/a.py", action="created"),
            FileTouched(path="src/missing.py", action="created"),
        ]),
    )
    ctx = ValidationContext(worktree_path=str(tmp_path))
    findings = FileCreationValidator().validate(report, ctx)
    # Only the missing one fires.
    assert len(findings) == 1
    assert "src/missing.py" in findings[0].detail


def test_deletion_validator_complains_when_file_still_there(tmp_path: Path) -> None:
    f = tmp_path / "still-here.txt"
    f.write_text("oops")
    report = CompletionReport(
        status="done",
        evidence=Evidence(files_touched=[
            FileTouched(path="still-here.txt", action="deleted"),
        ]),
    )
    ctx = ValidationContext(worktree_path=str(tmp_path))
    findings = FileDeletionValidator().validate(report, ctx)
    assert len(findings) == 1


# ── TestRunValidator ───────────────────────────────────────────────────────

def test_test_run_validator_passes_when_audit_matches() -> None:
    report = CompletionReport(
        status="done",
        evidence=Evidence(tests_run=[TestRun(command="pytest", exit_code=0)]),
    )
    ctx = ValidationContext(audit_rows=[
        CommandAuditRow(command="pytest", exit_code=0),
    ])
    assert TestRunValidator().validate(report, ctx) == []


def test_test_run_validator_flags_exit_code_mismatch() -> None:
    report = CompletionReport(
        status="done",
        evidence=Evidence(tests_run=[TestRun(command="pytest", exit_code=0)]),
    )
    ctx = ValidationContext(audit_rows=[
        CommandAuditRow(command="pytest", exit_code=1),
    ])
    findings = TestRunValidator().validate(report, ctx)
    assert len(findings) == 1
    assert "exit_code" in findings[0].detail


def test_test_run_validator_flags_command_never_ran() -> None:
    report = CompletionReport(
        status="done",
        evidence=Evidence(tests_run=[TestRun(command="pytest", exit_code=0)]),
    )
    findings = TestRunValidator().validate(report, ValidationContext())
    assert len(findings) == 1


# ── PackageInstallValidator ─────────────────────────────────────────────────

def test_package_validator_matches_by_base_name() -> None:
    report = CompletionReport(
        status="done",
        evidence=Evidence(packages_installed=["react@18.2", "zod"]),
    )
    ctx = ValidationContext(installed_packages_after={"react@18.2.0", "zod@3.22"})
    assert PackageInstallValidator().validate(report, ctx) == []


def test_package_validator_flags_missing() -> None:
    report = CompletionReport(
        status="done",
        evidence=Evidence(packages_installed=["leftpad"]),
    )
    findings = PackageInstallValidator().validate(report, ValidationContext())
    assert len(findings) == 1


# ── orchestrator-facing validate_report ──────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_report_async_aggregates_all_validators(tmp_path: Path) -> None:
    report = CompletionReport(
        status="done",
        description="Created auth.ts and ran tests",
        evidence=Evidence(
            files_touched=[FileTouched(path="auth.ts", action="created", lines_added=42)],
            tests_run=[TestRun(command="pytest", exit_code=0)],
        ),
    )
    # All claims are honest in this context.
    real_file = tmp_path / "auth.ts"
    real_file.write_text("export {}")
    ctx = ValidationContext(
        worktree_path=str(tmp_path),
        git_changes=[GitFileChange(
            path="auth.ts", is_new=True, is_deleted=False,
            lines_added=42, lines_removed=0,
        )],
        audit_rows=[CommandAuditRow(command="pytest", exit_code=0)],
    )
    result = await validate_report_async(report, ctx)
    assert result.passed is True
    assert result.has_critical_issues is False


def test_validate_report_catches_combined_issues() -> None:
    """One bad claim across multiple validators should produce findings from each."""
    report = CompletionReport(
        status="done",
        evidence=Evidence(
            files_touched=[FileTouched(path="ghost.py", action="created")],
            tests_run=[TestRun(command="never-ran", exit_code=0)],
            packages_installed=["non-existent-pkg"],
        ),
    )
    ctx = ValidationContext()  # no evidence supports any claim
    result = validate_report(report, ctx)
    assert not result.passed
    assert result.has_critical_issues
    # Multiple validators fired.
    sources = {f.validator for f in result.findings if not f.ok}
    assert "FileModificationValidator" in sources
    assert "TestRunValidator" in sources
    assert "PackageInstallValidator" in sources


# ── semantic cross-check (stub injection) ───────────────────────────────────

@pytest.mark.asyncio
async def test_semantic_cross_check_skipped_when_no_caller() -> None:
    report = CompletionReport(status="done", description="ok")
    result = await semantic_cross_check(report, ValidationContext())
    assert result.skipped
    assert result.skipped_reason == "not_wired"


@pytest.mark.asyncio
async def test_semantic_cross_check_skipped_for_failed_status() -> None:
    report = CompletionReport(status="failed", description="couldn't do it")
    result = await semantic_cross_check(report, ValidationContext())
    assert result.skipped
    assert result.skipped_reason == "status_failed"


@pytest.mark.asyncio
async def test_semantic_cross_check_parses_haiku_score() -> None:
    async def fake_haiku(_prompt: str) -> str:
        return "8.5  evidence supports the claim"
    report = CompletionReport(status="done", description="x")
    result = await semantic_cross_check(report, ValidationContext(), haiku_caller=fake_haiku)
    assert result.skipped is False
    assert result.score == pytest.approx(8.5)
    assert "evidence supports" in result.rationale.lower()


@pytest.mark.asyncio
async def test_semantic_cross_check_clamps_score_to_range() -> None:
    async def fake_haiku(_prompt: str) -> str:
        return "15 too generous"
    result = await semantic_cross_check(
        CompletionReport(status="done"), ValidationContext(), haiku_caller=fake_haiku,
    )
    assert result.score == 10.0


@pytest.mark.asyncio
async def test_semantic_cross_check_handles_garbage_response() -> None:
    async def fake_haiku(_prompt: str) -> str:
        return "lol idk"
    result = await semantic_cross_check(
        CompletionReport(status="done"), ValidationContext(), haiku_caller=fake_haiku,
    )
    assert result.score == 0.0
    assert "could not parse" in result.rationale.lower()


# ── Trust scores ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_completion_upserts(db: Path) -> None:
    s1 = await record_completion("builder-sonnet", passed_validation=True, db_path=db)
    assert s1.successful_completions == 1
    assert s1.total_sessions == 1
    assert s1.score == 1.0

    s2 = await record_completion("builder-sonnet", passed_validation=False, db_path=db)
    assert s2.successful_completions == 1
    assert s2.failed_validations == 1
    assert s2.total_sessions == 2
    assert s2.score == 0.5


@pytest.mark.asyncio
async def test_get_trust_score_returns_none_for_unknown(db: Path) -> None:
    assert await get_trust_score("never-seen", db_path=db) is None


@pytest.mark.asyncio
async def test_list_trust_scores_orders_by_sessions(db: Path) -> None:
    for _ in range(5):
        await record_completion("popular", passed_validation=True, db_path=db)
    await record_completion("niche", passed_validation=True, db_path=db)
    items = await list_trust_scores(db_path=db)
    assert items[0].worker_id == "popular"


@pytest.mark.asyncio
async def test_reset_trust_score_clears(db: Path) -> None:
    await record_completion("w", passed_validation=False, db_path=db)
    await reset_trust_score("w", db_path=db)
    assert await get_trust_score("w", db_path=db) is None


def test_is_low_trust_only_after_enough_data() -> None:
    from backend.validation.trust import TrustScore
    # A worker with 5 sessions at 50% is NOT marked low — not enough data.
    s = TrustScore("w", 2, 3, 5, 0.4, "ts")
    assert not is_low_trust(s)
    # With 20 sessions at 40%, the flag fires.
    s = TrustScore("w", 8, 12, 20, 0.4, "ts")
    assert is_low_trust(s)
    # Above the floor, even with lots of data — fine.
    s = TrustScore("w", 80, 20, 100, 0.8, "ts")
    assert not is_low_trust(s)
    # Unknown worker — innocent.
    assert not is_low_trust(None)


def test_trust_floor_constant_is_sane() -> None:
    assert 0.5 < LOW_TRUST_FLOOR < 1.0


# ── HTTP integration ────────────────────────────────────────────────────────

def test_validation_trust_endpoint_shape() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/validation/trust")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body and "low_trust_floor" in body


def test_validation_trust_404_unknown() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/validation/trust/__does_not_exist__")
    assert resp.status_code == 404


def test_validation_trust_delete_idempotent() -> None:
    with TestClient(app) as client:
        # Deleting something that doesn't exist still returns ok=True
        resp = client.delete("/api/validation/trust/__does_not_exist__")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
