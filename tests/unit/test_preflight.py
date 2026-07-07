"""Preflight + install endpoints — Phase 9C testing fixes #1, #4, #7, #8."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api import install_http
from backend.api.preflight_http import _git_identity
from backend.detection import BackendStatus
from backend.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ── preflight ───────────────────────────────────────────────────────────────

def test_preflight_passes_when_everything_is_set(client) -> None:
    happy = BackendStatus(claude_cli=True, claude_cli_version="2.1", claude_api=False,
                          ollama=True, ollama_models=["llama3.1"])
    with patch("backend.api.preflight_http.detect_backends",
               new_callable=AsyncMock, return_value=happy), \
         patch("backend.api.preflight_http._git_identity",
               new_callable=AsyncMock, return_value=("Alice", "alice@example.com")):
        resp = client.get("/api/preflight/check")
    body = resp.json()
    assert body["ok"] is True
    assert body["blockers"] == []


def test_preflight_blocks_when_git_identity_missing(client) -> None:
    happy = BackendStatus(claude_cli=True, claude_cli_version="2.1", claude_api=False,
                          ollama=True, ollama_models=[])
    with patch("backend.api.preflight_http.detect_backends",
               new_callable=AsyncMock, return_value=happy), \
         patch("backend.api.preflight_http._git_identity",
               new_callable=AsyncMock, return_value=("", "")):
        resp = client.get("/api/preflight/check")
    body = resp.json()
    assert body["ok"] is False
    blocker_ids = [b["id"] for b in body["blockers"]]
    assert "git-identity" in blocker_ids
    # auto_fixable is exposed so the UI can offer the "Configure for me" button
    git_blocker = next(b for b in body["blockers"] if b["id"] == "git-identity")
    assert git_blocker["auto_fixable"] is True


def test_preflight_blocks_when_no_claude_backend(client) -> None:
    sad = BackendStatus(claude_cli=False, claude_cli_version="", claude_api=False)
    with patch("backend.api.preflight_http.detect_backends",
               new_callable=AsyncMock, return_value=sad), \
         patch("backend.api.preflight_http._git_identity",
               new_callable=AsyncMock, return_value=("A", "a@b.com")):
        resp = client.get("/api/preflight/check")
    body = resp.json()
    assert body["ok"] is False
    assert any(b["id"] == "no-claude" for b in body["blockers"])


@pytest.mark.asyncio
async def test_git_identity_returns_empty_when_unconfigured() -> None:
    """When git isn't installed at all, _git_identity returns ('', '')."""
    with patch("backend.api.preflight_http.shutil.which", return_value=None):
        name, email = await _git_identity()
    assert name == ""
    assert email == ""


# ── registry diagnose ──────────────────────────────────────────────────────

def test_registry_diagnose_endpoint_shape(client) -> None:
    resp = client.get("/api/registries/diagnose")
    assert resp.status_code == 200
    body = resp.json()
    assert "skills" in body and "mcp" in body
    skills_sources = {s["name"] for s in body["skills"]["sources"]}
    assert skills_sources == {"clawhub", "cookbook", "community"}
    mcp_sources = {s["name"] for s in body["mcp"]["sources"]}
    assert mcp_sources == {"official", "smithery"}
    # Each source row has the diagnostic fields we depend on in the UI
    for row in body["skills"]["sources"]:
        for field in ("ok", "error", "items_returned", "duration_ms", "last_success_at"):
            assert field in row


# ── skill install ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skill_install_writes_skill_md_and_imports(monkeypatch, tmp_path) -> None:
    # Redirect SKILLS_ROOT for this test so we don't pollute the user's ~/.hive
    monkeypatch.setattr(install_http, "SKILLS_ROOT", tmp_path)

    fake_imported = []

    async def fake_import(path):
        fake_imported.append(path)
        from backend.skills.registry import Skill
        return Skill(id="cookbook-test", name="cookbook-test",
                     description="x", tags=[], path=str(path),
                     instructions="...", version=1)

    monkeypatch.setattr(install_http, "import_skill", fake_import)

    from backend.api.install_http import SkillInstallRequest, skill_install
    res = await skill_install(SkillInstallRequest(
        id="cookbook/test",
        name="cookbook-test",
        description="hello",
        source="cookbook",
        url=None,
        tags=["python"],
    ))
    assert res["ok"] is True
    assert fake_imported, "import_skill was never called"
    written = fake_imported[0]
    assert Path(written).exists()
    assert 'name: "cookbook-test"' in Path(written).read_text()


# ── MCP install ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_install_writes_to_claude_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "claude.json"
    monkeypatch.setattr(install_http, "_claude_config_path", lambda: config_path)

    from backend.api.install_http import MCPInstallRequest, mcp_install
    res = await mcp_install(MCPInstallRequest(
        id="mcp/filesystem",
        name="Filesystem",
        install={"transport": "npm", "package": "@modelcontextprotocol/server-filesystem"},
        permissions=["files: read/write"],
    ))
    assert res["ok"] is True
    assert "npm install -g" in res["command"]

    data = json.loads(config_path.read_text())
    assert "mcpServers" in data
    assert "filesystem" in data["mcpServers"]
    entry = data["mcpServers"]["filesystem"]
    assert entry["command"] == "npx"
    assert "@modelcontextprotocol/server-filesystem" in entry["args"]


@pytest.mark.asyncio
async def test_mcp_uninstall_removes_entry(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "claude.json"
    config_path.write_text(json.dumps({
        "mcpServers": {"filesystem": {"command": "npx", "args": ["server"]}},
    }))
    monkeypatch.setattr(install_http, "_claude_config_path", lambda: config_path)

    from backend.api.install_http import mcp_uninstall
    res = await mcp_uninstall("mcp/filesystem")
    assert res["ok"] is True
    assert res["removed"] is True

    data = json.loads(config_path.read_text())
    assert "filesystem" not in data.get("mcpServers", {})


# ── install URL allowlist (SSRF guard) ────────────────────────────────────

def test_validate_install_url_rejects_http_scheme() -> None:
    from backend.api.install_http import _validate_install_url
    with pytest.raises(HTTPException) as exc:
        _validate_install_url("http://github.com/anthropics/anthropic-cookbook")
    assert exc.value.status_code == 400
    assert "https" in exc.value.detail


def test_validate_install_url_rejects_non_allowlisted_host() -> None:
    from backend.api.install_http import _validate_install_url
    with pytest.raises(HTTPException) as exc:
        _validate_install_url("https://evil.example.com/payload.md")
    assert exc.value.status_code == 400
    assert "allowed" in exc.value.detail.lower()


def test_validate_install_url_rejects_loopback_hostname() -> None:
    from backend.api.install_http import _validate_install_url
    with pytest.raises(HTTPException) as exc:
        _validate_install_url("https://localhost:8765/skill.md")
    assert exc.value.status_code == 400
    assert "localhost" in exc.value.detail


def test_validate_install_url_rejects_loopback_ip_literal() -> None:
    from backend.api.install_http import _validate_install_url
    with pytest.raises(HTTPException) as exc:
        _validate_install_url("https://127.0.0.1:8765/skill.md")
    assert exc.value.status_code == 400


def test_validate_install_url_allows_github() -> None:
    from backend.api.install_http import _validate_install_url
    _validate_install_url(
        "https://github.com/anthropics/anthropic-cookbook/tree/main/skills/code-review"
    )


def test_validate_install_url_allows_raw_github() -> None:
    from backend.api.install_http import _validate_install_url
    _validate_install_url(
        "https://raw.githubusercontent.com/anthropics/anthropic-cookbook/main/skills/x/SKILL.md"
    )


def test_validate_install_url_blocks_dns_rebind_to_private(monkeypatch) -> None:
    """An allowlisted host that resolves to a private IP must be rejected."""
    import socket as _socket
    from backend.api import install_http as _ih

    def fake_getaddrinfo(host, _port, *_a, **_kw):
        return [(_socket.AF_INET, _socket.SOCK_STREAM, 0, "", ("10.0.0.5", 0))]

    monkeypatch.setattr(_ih.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(HTTPException) as exc:
        _ih._validate_install_url("https://github.com/owner/repo/tree/main/x")
    assert exc.value.status_code == 400
    assert "non-public" in exc.value.detail


@pytest.mark.asyncio
async def test_skill_install_blocks_ssrf_at_endpoint(monkeypatch, tmp_path) -> None:
    """End-to-end: a malicious URL through the public endpoint is refused."""
    monkeypatch.setattr(install_http, "SKILLS_ROOT", tmp_path)

    from backend.api.install_http import SkillInstallRequest, skill_install
    with pytest.raises(HTTPException) as exc:
        await skill_install(SkillInstallRequest(
            id="evil",
            name="evil",
            description="x",
            source="cookbook",
            url="http://127.0.0.1:8765/admin/wipe",
            tags=[],
        ))
    assert exc.value.status_code == 400


# ── claude.json corruption refuses to overwrite ───────────────────────────

def test_read_claude_config_raises_on_corrupt_json_and_writes_backup(tmp_path) -> None:
    """Corrupt JSON must raise (not return {}) so the caller does not
    proceed to overwrite the file with a fresh config that wipes every
    non-MCP key the user had set."""
    from backend.api.install_http import _read_claude_config

    config = tmp_path / "claude.json"
    original = '{"this is not json", "memory": {"important": "data"}}'
    config.write_text(original, encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        _read_claude_config(config)
    assert exc.value.status_code == 500
    assert "backup" in exc.value.detail.lower()

    # Original must NOT have been overwritten.
    assert config.read_text(encoding="utf-8") == original
    # A backup must exist alongside it.
    backups = list(tmp_path.glob("claude.json.corrupted.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_mcp_install_refuses_to_wipe_corrupt_config(monkeypatch, tmp_path) -> None:
    """End-to-end: mcp_install raises 500 instead of silently overwriting
    a corrupt ~/.claude.json with a fresh one."""
    config_path = tmp_path / "claude.json"
    original = '{"broken json'
    config_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(install_http, "_claude_config_path", lambda: config_path)

    from backend.api.install_http import MCPInstallRequest, mcp_install
    with pytest.raises(HTTPException) as exc:
        await mcp_install(MCPInstallRequest(
            id="mcp/x", name="x",
            install={"transport": "npm", "package": "p"},
            permissions=[],
        ))
    assert exc.value.status_code == 500
    assert config_path.read_text(encoding="utf-8") == original


# Suppress unused-import warning when running this file in isolation.
_ = (asyncio, os)
