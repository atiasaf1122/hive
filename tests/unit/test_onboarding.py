"""Onboarding-flow tests — checks, report rendering, idempotency."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.detection import BackendStatus
from backend.onboarding import (
    Check,
    check_backends,
    check_credentials_file,
    check_git,
    initialise_data_dir,
    render_report,
    run_onboarding,
)


# ── Check dataclass ─────────────────────────────────────────────────────────

def test_check_status_icon_ok() -> None:
    assert Check("x", True).status_icon() == "✓"


def test_check_status_icon_fail() -> None:
    assert Check("x", False).status_icon() == "✗"


# ── individual checks ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_git_uses_real_git_when_available() -> None:
    """The CI env has git; we just assert the check runs and produces a sensible result."""
    result = await check_git()
    assert result.name == "git"
    if result.ok:
        assert "git version" in result.detail


@pytest.mark.asyncio
async def test_check_git_missing() -> None:
    """If `which git` returns None, the check fails cleanly with an install hint."""
    with patch("backend.onboarding.shutil.which", return_value=None):
        result = await check_git()
    assert result.ok is False
    assert "Install git" in result.hint


@pytest.mark.asyncio
async def test_check_backends_translates_status() -> None:
    fake_status = BackendStatus(
        claude_cli=True, claude_cli_version="2.1",
        claude_api=False,
        ollama=True, ollama_models=["llama3.1"],
    )
    with patch("backend.onboarding.detect_backends",
               new_callable=AsyncMock, return_value=fake_status):
        checks = await check_backends()

    by_name = {c.name: c for c in checks}
    assert by_name["claude CLI"].ok is True
    assert by_name["claude API key"].ok is False
    assert by_name["Ollama"].ok is True
    assert "llama3.1" in by_name["Ollama"].detail


def test_check_credentials_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.onboarding.HIVE_DIR", tmp_path)
    result = check_credentials_file()
    assert result.ok is False
    assert "setup-token" in result.hint


def test_check_credentials_present_correct_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.onboarding.HIVE_DIR", tmp_path)
    creds = tmp_path / "credentials.json"
    creds.write_text("{}")
    os.chmod(creds, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    result = check_credentials_file()
    assert result.ok is True
    assert "0600" in result.detail


def test_check_credentials_wrong_mode_flags_hint(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.onboarding.HIVE_DIR", tmp_path)
    creds = tmp_path / "credentials.json"
    creds.write_text("{}")
    os.chmod(creds, 0o644)  # world-readable — bad
    result = check_credentials_file()
    # File exists so ok=True, but the hint warns about mode
    assert result.ok is True
    assert "0644" in result.detail
    assert "chmod 600" in result.hint


# ── initialise_data_dir is idempotent ────────────────────────────────────────

@pytest.mark.asyncio
async def test_initialise_data_dir_creates_db(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "hive.db"
    monkeypatch.setattr("backend.onboarding.HIVE_DIR", tmp_path)
    monkeypatch.setattr("backend.onboarding.ensure_hive_dir", lambda: tmp_path)
    monkeypatch.setattr("backend.onboarding.init_db", AsyncMock())

    result = await initialise_data_dir()
    assert result.ok is True


@pytest.mark.asyncio
async def test_initialise_data_dir_reports_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.onboarding.HIVE_DIR", tmp_path)
    monkeypatch.setattr("backend.onboarding.ensure_hive_dir", lambda: tmp_path)
    monkeypatch.setattr("backend.onboarding.init_db",
                        AsyncMock(side_effect=RuntimeError("boom")))
    result = await initialise_data_dir()
    assert result.ok is False
    assert "boom" in result.detail


# ── report rendering ────────────────────────────────────────────────────────

def test_render_report_all_pass() -> None:
    checks = [Check("a", True, "ok"), Check("b", True, "ok2")]
    report = render_report(checks)
    assert "All checks passed" in report
    assert "issue(s)" not in report


def test_render_report_with_failures_shows_count_and_hints() -> None:
    checks = [
        Check("a", True),
        Check("b", False, "missing", "install b"),
        Check("c", False, "broken"),
    ]
    report = render_report(checks)
    assert "2 issue(s)" in report
    assert "install b" in report


# ── full onboarding pass ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_onboarding_returns_all_checks(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.onboarding.HIVE_DIR", tmp_path)
    monkeypatch.setattr("backend.onboarding.ensure_hive_dir", lambda: tmp_path)
    monkeypatch.setattr("backend.onboarding.init_db", AsyncMock())

    fake_status = BackendStatus(claude_cli=False, claude_api=False, ollama=False)
    monkeypatch.setattr(
        "backend.onboarding.detect_backends",
        AsyncMock(return_value=fake_status),
    )

    checks = await run_onboarding()
    names = [c.name for c in checks]
    # Order: data dir, git, claude CLI, claude API, Ollama, OAuth token
    assert names[0] == "data dir"
    assert names[1] == "git"
    assert "claude CLI" in names
    assert "claude API key" in names
    assert "Ollama" in names
    assert "claude OAuth token" in names
