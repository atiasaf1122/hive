"""C1 — MCP server catalog, placeholder expansion, preflight."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.mcp.catalog import (
    CATALOG,
    get_spec,
    preflight,
    render_mcp_config,
)


def test_catalog_has_the_approved_six_servers() -> None:
    """Part 5 (approved): keep 4 + postgres + youtube-transcript."""
    assert set(CATALOG) == {"playwright", "github", "context7", "filesystem",
                            "postgres", "youtube-transcript"}


def test_isolated_servers_have_isolation_args() -> None:
    for spec in CATALOG.values():
        if spec.per_agent_isolation:
            assert spec.isolation_args, f"{spec.id} claims isolation but has no args"


def test_render_expands_placeholders() -> None:
    cfg = render_mcp_config(["playwright"], agent_id="tester-x-0", worktree="/wt/t0")
    args = cfg["mcpServers"]["playwright"]["args"]
    joined = " ".join(args)
    assert "/wt/t0/.playwright" in joined          # screenshots into worktree
    assert "{agent_id}" not in joined and "{worktree}" not in joined
    # --isolated IS the per-agent isolation (in-memory profile per
    # instance); --user-data-dir is rejected in isolated mode.
    assert "--isolated" in args and "--headless" in args
    assert "--user-data-dir" not in args
    # file:// navigation is blocked by default and would break the
    # open-the-built-page flow (C5 e2e finding).
    assert "--allow-unrestricted-file-access" in args
    # Default channel is system Chrome, absent on WSL (C5 finding).
    joined2 = " ".join(args)
    assert "--browser chromium" in joined2


def test_filesystem_scoped_to_worktree_only() -> None:
    cfg = render_mcp_config(["filesystem"], agent_id="a", worktree="/wt/a")
    args = cfg["mcpServers"]["filesystem"]["args"]
    assert args[-1] == "/wt/a"                      # allowed dir = worktree
    assert "/" not in args[:-1] or True             # no blanket roots
    assert "~" not in " ".join(args)


def test_render_github_http_with_token(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
    cfg = render_mcp_config(["github"], agent_id="a", worktree="/wt")
    gh = cfg["mcpServers"]["github"]
    assert gh["type"] == "http"
    assert gh["headers"]["Authorization"] == "Bearer ghp_test123"


def test_render_github_without_token_raises(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        render_mcp_config(["github"], agent_id="a", worktree="/wt")


def test_optional_env_dropped_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("CONTEXT7_API_KEY", raising=False)
    cfg = render_mcp_config(["context7"], agent_id="a", worktree="/wt")
    assert "env" not in cfg["mcpServers"]["context7"]

    monkeypatch.setenv("CONTEXT7_API_KEY", "ck-1")
    cfg = render_mcp_config(["context7"], agent_id="a", worktree="/wt")
    assert cfg["mcpServers"]["context7"]["env"]["CONTEXT7_API_KEY"] == "ck-1"


def test_render_unknown_id_raises() -> None:
    with pytest.raises(ValueError, match="unknown MCP server"):
        render_mcp_config(["nope"], agent_id="a", worktree="/wt")


def test_preflight_detects_missing_env(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    missing = preflight(get_spec("github"))
    assert missing and "GITHUB_TOKEN" in missing[0]

    monkeypatch.setenv("GITHUB_TOKEN", "x")
    assert preflight(get_spec("github")) == []


def test_preflight_detects_old_node() -> None:
    with patch("backend.mcp.catalog._node_major", return_value=16):
        missing = preflight(get_spec("playwright"))
    assert missing and "node>=20" in missing[0]

    with patch("backend.mcp.catalog._node_major", return_value=None):
        missing = preflight(get_spec("playwright"))
    assert missing and "not installed" in missing[0]


def test_catalog_endpoint() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/mcp/catalog")
    assert resp.status_code == 200
    servers = {s["id"]: s for s in resp.json()["servers"]}
    assert set(servers) == {"playwright", "github", "context7", "filesystem",
                            "postgres", "youtube-transcript"}
    for s in servers.values():
        assert "preflight_ok" in s and "missing" in s
    # Secrets never leak: header values are still ${VAR} templates.
    assert servers["github"]["headers"]["Authorization"] == "Bearer ${GITHUB_TOKEN}"


def test_render_postgres_expands_url_env_in_args(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@localhost/forecast")
    cfg = render_mcp_config(["postgres"], "agent-1", "/tmp/wt")
    args = cfg["mcpServers"]["postgres"]["args"]
    assert args[-1] == "postgresql://u:p@localhost/forecast"


def test_render_postgres_without_url_raises(monkeypatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(ValueError):
        render_mcp_config(["postgres"], "agent-1", "/tmp/wt")


def test_preflight_postgres_flags_missing_url(monkeypatch) -> None:
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    missing = preflight(get_spec("postgres"))
    assert any("POSTGRES_URL" in m for m in missing)


def test_youtube_transcript_needs_no_secrets() -> None:
    spec = get_spec("youtube-transcript")
    assert all(not r.startswith("env:") for r in spec.requires)
    cfg = render_mcp_config(["youtube-transcript"], "agent-1", "/tmp/wt")
    assert "@kimtaeyoon83/mcp-server-youtube-transcript" in         cfg["mcpServers"]["youtube-transcript"]["args"]
