"""POST /api/sessions workspace-path validation.

Before this guard, an empty / missing / non-directory `project_path`
fell through to the worktree manager and surfaced as an opaque
``FileNotFoundError`` from uvloop's subprocess plumbing. We now refuse
the request with a clear 400 — and the worktree manager has a
defensive raise for the programmatic-bypass case.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api import http as http_mod
from backend.main import app
from backend.worktrees.manager import WorktreeManager


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    http_mod._pending_approvals.clear()
    http_mod._pending_inputs.clear()
    http_mod._running_tasks.clear()
    http_mod._message_queues.clear()


@pytest.fixture(autouse=True)
def _stub_launch():
    """We're only exercising the validation path — keep launch_session a no-op."""
    with patch("backend.api.http.launch_session") as mocked:
        mocked.return_value = None
        yield mocked


def _payload(**overrides) -> dict:
    base = {
        "task": "do a thing",
        "model": "claude:sonnet",
        "approval_mode": "full-auto",
        "max_turns": 20,
    }
    base.update(overrides)
    return base


# ── HTTP-level guards ──────────────────────────────────────────────────────


def test_session_create_with_empty_path_returns_400(client: TestClient) -> None:
    resp = client.post("/api/sessions", json=_payload(project_path=""))
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "empty" in detail.lower()


def test_session_create_with_whitespace_path_returns_400(client: TestClient) -> None:
    resp = client.post("/api/sessions", json=_payload(project_path="   "))
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_session_create_with_missing_path_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/api/sessions",
        json=_payload(project_path="/this/definitely/does/not/exist/__nope__"),
    )
    assert resp.status_code == 400
    assert "does not exist" in resp.json()["detail"].lower()


def test_session_create_with_path_pointing_to_file_returns_400(
    tmp_path,
    client: TestClient,
) -> None:
    target = tmp_path / "not_a_dir.txt"
    target.write_text("hello", encoding="utf-8")
    resp = client.post("/api/sessions", json=_payload(project_path=str(target)))
    assert resp.status_code == 400
    assert "not a directory" in resp.json()["detail"].lower()


def test_session_create_expands_tilde(tmp_path, client: TestClient, monkeypatch) -> None:
    """The validator must run Path.expanduser() so ~/projects works."""
    fake_home = tmp_path / "user-home"
    fake_home.mkdir()
    (fake_home / "projects").mkdir()

    monkeypatch.setenv("HOME", str(fake_home))
    # Path.expanduser reads $HOME on POSIX; that's all we need.

    resp = client.post("/api/sessions", json=_payload(project_path="~/projects"))
    assert resp.status_code == 200, resp.text


def test_session_create_with_valid_dir_succeeds(tmp_path, client: TestClient) -> None:
    resp = client.post(
        "/api/sessions",
        json=_payload(project_path=str(tmp_path)),
    )
    assert resp.status_code == 200, resp.text
    assert "session_id" in resp.json()


def test_session_create_without_path_uses_session_default(client: TestClient) -> None:
    """Omitting project_path entirely keeps the old fallback to ~/.hive/sessions/<id>/."""
    resp = client.post("/api/sessions", json=_payload())
    assert resp.status_code == 200, resp.text


# ── Defensive raise inside WorktreeManager ─────────────────────────────────


@pytest.mark.asyncio
async def test_worktree_manager_empty_path_raises_value_error() -> None:
    mgr = WorktreeManager(session_id="sess-empty", project_path="")
    with pytest.raises(ValueError, match="Project path missing"):
        await mgr.ensure_git_repo()


@pytest.mark.asyncio
async def test_worktree_manager_whitespace_path_raises_value_error() -> None:
    mgr = WorktreeManager(session_id="sess-ws", project_path="   ")
    with pytest.raises(ValueError, match="Project path missing"):
        await mgr.ensure_git_repo()


@pytest.mark.asyncio
async def test_worktree_manager_nonexistent_path_raises_value_error(tmp_path) -> None:
    ghost = tmp_path / "no-such-dir"
    # Don't create it.
    mgr = WorktreeManager(session_id="sess-ghost", project_path=str(ghost))
    with pytest.raises(ValueError, match="Project path missing"):
        await mgr.ensure_git_repo()
