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


# ── Windows-on-WSL path normalisation ──────────────────────────────────────


from backend.api.http import (  # noqa: E402  (after-fixture imports for readability)
    _normalize_workspace_path,
    _windows_to_wsl,
)


def test_windows_to_wsl_backslash_form() -> None:
    assert _windows_to_wsl(r"C:\Users\foo\bar") == "/mnt/c/Users/foo/bar"


def test_windows_to_wsl_forward_slash_form() -> None:
    assert _windows_to_wsl("C:/Users/foo/bar") == "/mnt/c/Users/foo/bar"


def test_windows_to_wsl_lowercases_drive_letter() -> None:
    assert _windows_to_wsl(r"D:\projects") == "/mnt/d/projects"


def test_windows_to_wsl_passes_non_windows_input_through() -> None:
    assert _windows_to_wsl("/home/user/projects") == "/home/user/projects"


def test_normalize_converts_windows_path_when_wsl(monkeypatch) -> None:
    monkeypatch.setattr("backend.api.http._is_wsl", lambda: True)
    assert str(_normalize_workspace_path(r"C:\Users\foo")) == "/mnt/c/Users/foo"


def test_normalize_keeps_wsl_path_unchanged(monkeypatch) -> None:
    monkeypatch.setattr("backend.api.http._is_wsl", lambda: True)
    assert str(_normalize_workspace_path("/mnt/c/Users/foo")) == "/mnt/c/Users/foo"


def test_normalize_keeps_linux_path_unchanged(monkeypatch) -> None:
    monkeypatch.setattr("backend.api.http._is_wsl", lambda: True)
    assert str(_normalize_workspace_path("/home/user/projects")) == "/home/user/projects"


def test_normalize_leaves_windows_path_unrewritten_on_non_wsl(monkeypatch) -> None:
    """On a real Linux box (not WSL), a Windows-shaped path is gibberish.
    We don't rewrite it; the existence check downstream rejects it."""
    monkeypatch.setattr("backend.api.http._is_wsl", lambda: False)
    # The expanduser pass-through preserves the raw form on POSIX.
    out = str(_normalize_workspace_path(r"C:\Users\foo"))
    assert "/mnt/" not in out


def test_session_create_converts_windows_path_when_wsl(
    tmp_path,
    client: TestClient,
    monkeypatch,
) -> None:
    """End-to-end: a Windows path goes in, the backend translates it to
    /mnt/<drive>/… , the file is found, the session is created."""
    # Build a fake "/mnt/c/Projects/x" → tmp_path tree.
    mnt_root = tmp_path / "mnt" / "c" / "Projects" / "x"
    mnt_root.mkdir(parents=True)

    monkeypatch.setattr("backend.api.http._is_wsl", lambda: True)

    # Stub _windows_to_wsl so the C: prefix maps onto our tmp_path. The
    # production version returns "/mnt/c/Projects/x"; the test version
    # returns the tmp_path so .exists() is True without touching /mnt/.
    real_to_wsl = __import__("backend.api.http", fromlist=["_windows_to_wsl"])._windows_to_wsl
    expected = real_to_wsl(r"C:\Projects\x")
    assert expected == "/mnt/c/Projects/x"

    monkeypatch.setattr(
        "backend.api.http._windows_to_wsl",
        lambda raw: str(mnt_root) if raw.lower().startswith("c:") else raw,
    )

    resp = client.post(
        "/api/sessions",
        json=_payload(project_path=r"C:\Projects\x"),
    )
    assert resp.status_code == 200, resp.text


def test_session_create_windows_path_on_non_wsl_hints_at_translation(
    client: TestClient,
    monkeypatch,
) -> None:
    """On non-WSL, a Windows path 400s with a clarifying hint."""
    monkeypatch.setattr("backend.api.http._is_wsl", lambda: False)
    resp = client.post(
        "/api/sessions",
        json=_payload(project_path=r"C:\Users\nope\nada"),
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "does not exist" in detail
    assert "wsl" in detail


# ── /api/detect/host surface ───────────────────────────────────────────────


def test_detect_host_reports_wsl_flag(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("backend.api.http._is_wsl", lambda: True)
    resp = client.get("/api/detect/host")
    assert resp.status_code == 200
    body = resp.json()
    assert body["wsl"] is True
    assert "system" in body


def test_detect_host_reports_non_wsl(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("backend.api.http._is_wsl", lambda: False)
    resp = client.get("/api/detect/host")
    assert resp.status_code == 200
    assert resp.json()["wsl"] is False
