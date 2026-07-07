"""Registry proxy tests — cache, fallback, security flags.

We never let these tests hit the real ClawHub / GitHub / Smithery APIs;
every fetcher is monkeypatched so the suite stays fast and deterministic.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.registries import mcp, skills
from backend.registries.cache import TtlCache


# ── TtlCache primitive ───────────────────────────────────────────────────────

def test_cache_returns_stored_value() -> None:
    c = TtlCache(ttl_seconds=10)
    c.set("k", {"hello": "world"})
    assert c.get("k") == {"hello": "world"}


def test_cache_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    import time as time_mod
    base = 1000.0
    monkeypatch.setattr(time_mod, "time", lambda: base)

    c = TtlCache(ttl_seconds=10)
    c.set("k", "v")
    assert c.get("k") == "v"

    monkeypatch.setattr(time_mod, "time", lambda: base + 20)
    assert c.get("k") is None


# ── Skills proxy ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_caches():
    skills._CACHE.clear()
    mcp._CACHE.clear()


@pytest.mark.asyncio
async def test_skills_search_falls_back_when_all_fetchers_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(_q): raise RuntimeError("offline")
    monkeypatch.setattr(skills, "_fetch_clawhub", _boom)
    monkeypatch.setattr(skills, "_fetch_cookbook", _boom)
    monkeypatch.setattr(skills, "_fetch_community", _boom)

    res = await skills.search_skills(query=None, source="all")
    assert res["fallback"] is True
    assert res["sources_failed"] == ["clawhub", "cookbook", "community"]
    assert len(res["items"]) > 0
    for item in res["items"]:
        # safety annotations should always be present
        assert "warn_unverified" in item
        assert "auto_install_ok" in item


@pytest.mark.asyncio
async def test_skills_search_merges_live_and_curated(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_clawhub(_q):
        return [
            {
                "id": "clawhub/totally-new",
                "name": "totally-new",
                "description": "Brand new live entry.",
                "source": "clawhub",
                "source_label": "ClawHub",
                "url": "https://clawhub.dev/skills/totally-new",
                "tags": ["live"],
                "stars": 5,
                "downloads": 12,
                "verified": False,
            }
        ]
    async def fake_empty(_q): return []
    monkeypatch.setattr(skills, "_fetch_clawhub", fake_clawhub)
    monkeypatch.setattr(skills, "_fetch_cookbook", fake_empty)
    monkeypatch.setattr(skills, "_fetch_community", fake_empty)

    res = await skills.search_skills(source="all")
    assert res["fallback"] is False

    ids = {i["id"] for i in res["items"]}
    assert "clawhub/totally-new" in ids
    # Curated cookbook entries should still appear (fallback merge)
    assert any(i["id"].startswith("cookbook/") for i in res["items"])

    # Verification flags propagate
    new_item = next(i for i in res["items"] if i["id"] == "clawhub/totally-new")
    assert new_item["verified"] is False
    assert new_item["warn_unverified"] is True
    assert new_item["auto_install_ok"] is False     # stars < 100


@pytest.mark.asyncio
async def test_skills_search_query_filters_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_empty(_q): return []
    monkeypatch.setattr(skills, "_fetch_clawhub", fake_empty)
    monkeypatch.setattr(skills, "_fetch_cookbook", fake_empty)
    monkeypatch.setattr(skills, "_fetch_community", fake_empty)

    res = await skills.search_skills(query="python", source="all")
    # Each result must mention the query in name, description, OR tags.
    for item in res["items"]:
        haystack = (
            item["name"].lower()
            + " "
            + item["description"].lower()
            + " "
            + " ".join((t or "").lower() for t in item.get("tags", []))
        )
        assert "python" in haystack, f"{item['id']} doesn't actually match 'python'"


@pytest.mark.asyncio
async def test_skills_high_star_community_item_is_auto_install_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_community(_q):
        return [{
            "id": "github/awesome-skill",
            "name": "awesome-skill",
            "description": "...",
            "source": "community",
            "source_label": "GitHub",
            "url": "https://github.com/x/y",
            "tags": [],
            "stars": 500,
            "downloads": None,
            "verified": False,   # not in trusted publishers...
        }]
    async def fake_empty(_q): return []
    monkeypatch.setattr(skills, "_fetch_clawhub", fake_empty)
    monkeypatch.setattr(skills, "_fetch_cookbook", fake_empty)
    monkeypatch.setattr(skills, "_fetch_community", fake_community)

    res = await skills.search_skills(source="community")
    new = next(i for i in res["items"] if i["id"] == "github/awesome-skill")
    # ... but 500 stars clears the auto-install floor of 100
    assert new["auto_install_ok"] is True


@pytest.mark.asyncio
async def test_skills_cache_hit_skips_fetchers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"clawhub": 0}
    async def fake_clawhub(_q):
        calls["clawhub"] += 1
        return []
    async def fake_empty(_q): return []
    monkeypatch.setattr(skills, "_fetch_clawhub", fake_clawhub)
    monkeypatch.setattr(skills, "_fetch_cookbook", fake_empty)
    monkeypatch.setattr(skills, "_fetch_community", fake_empty)

    await skills.search_skills(source="all")
    await skills.search_skills(source="all")
    assert calls["clawhub"] == 1


# ── MCP proxy ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_falls_back_when_all_fetchers_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(): raise RuntimeError("offline")
    monkeypatch.setattr(mcp, "_fetch_official", _boom)
    monkeypatch.setattr(mcp, "_fetch_smithery", _boom)

    res = await mcp.list_mcp_servers(source="all")
    assert res["fallback"] is True
    assert res["sources_failed"] == ["official", "smithery"]
    assert len(res["items"]) > 0
    # Categories list populated from items
    assert isinstance(res["categories"], list)


@pytest.mark.asyncio
async def test_mcp_category_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _empty(): return []
    monkeypatch.setattr(mcp, "_fetch_official", _empty)
    monkeypatch.setattr(mcp, "_fetch_smithery", _empty)

    res = await mcp.list_mcp_servers(source="all", category="web search")
    assert all(i["category"] == "web search" for i in res["items"])
    assert len(res["items"]) >= 1


@pytest.mark.asyncio
async def test_mcp_query_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _empty(): return []
    monkeypatch.setattr(mcp, "_fetch_official", _empty)
    monkeypatch.setattr(mcp, "_fetch_smithery", _empty)

    res = await mcp.list_mcp_servers(query="postgres", source="all")
    names = " ".join(i["name"].lower() for i in res["items"])
    assert "postgres" in names


# ── HTTP integration ────────────────────────────────────────────────────────

def test_skills_search_endpoint_removed() -> None:
    """Close-out: the online skills-search proxy is gone — discovery lives
    only inside the sync flow (Skills page browses the local library)."""
    with TestClient(app) as client:
        resp = client.get("/api/registries/skills/search?source=all")
    assert resp.status_code == 404


def test_mcp_list_endpoint() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/registries/mcp/list?source=all")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("items", "fallback", "sources_tried", "sources_failed", "categories"):
        assert key in body


def test_skills_installed_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /skills/installed returns registry skills keyed by slug id."""
    from backend.api import install_http
    from backend.skills.registry import Skill

    async def fake_list_skills():  # noqa: ANN202
        return [
            Skill(id="git-hygiene", name="Git Hygiene", description="d",
                  tags=["git"], path="/tmp/x", instructions="i", version=2),
        ]

    monkeypatch.setattr(install_http, "list_skills", fake_list_skills)
    with TestClient(app) as client:
        resp = client.get("/api/registries/skills/installed")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items == [{
        "id": "git-hygiene", "name": "Git Hygiene", "description": "d",
        "tags": ["git"], "version": 2, "family": "misc",
    }]


def test_mcp_installed_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """GET /mcp/installed reads mcpServers from the Claude config file."""
    import json as json_mod

    from backend.api import install_http

    cfg = tmp_path / "claude.json"
    cfg.write_text(json_mod.dumps({
        "mcpServers": {
            "postgres-mcp": {"command": "npx", "args": ["-y", "pg-mcp"]},
            "broken-entry": "not-a-dict",  # tolerated, skipped
        }
    }))
    monkeypatch.setattr(install_http, "_claude_config_path", lambda: cfg)
    with TestClient(app) as client:
        resp = client.get("/api/registries/mcp/installed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["config_path"] == str(cfg)
    assert body["items"] == [
        {"key": "postgres-mcp", "command": "npx", "args": ["-y", "pg-mcp"]},
    ]


def test_mcp_installed_endpoint_no_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A missing config file yields an empty list, not an error."""
    from backend.api import install_http

    monkeypatch.setattr(
        install_http, "_claude_config_path", lambda: tmp_path / "absent.json"
    )
    with TestClient(app) as client:
        resp = client.get("/api/registries/mcp/installed")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_usage_summary_endpoint() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/usage/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "claude" in body and "ollama" in body and "notes" in body
    assert body["claude"]["burn_ratio"] >= 0
    assert isinstance(body["notes"], list) and len(body["notes"]) >= 2


# Quiet down the asyncio mode warning when pytest-asyncio runs these.
_ = asyncio
