"""Part 4 — local skills library sync: family classification, dedupe,
organize-on-disk, registry import, diff report, raw-URL upgrade."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from backend.api.install_http import _to_raw_url
from backend.skills.sync import classify_family, sync_skills


def _fake_embed(text: str) -> np.ndarray:
    vec = np.zeros(4, dtype=np.float32)
    vec[0] = float(len(text) % 7)
    return vec


# ── family classification ─────────────────────────────────────────────────────

def test_family_from_tags_first_match_wins() -> None:
    assert classify_family("x", "", ["react", "python"]) == "frontend"
    assert classify_family("x", "", ["docker", "kubernetes"]) == "devops"
    assert classify_family("db helper", "postgres migrations", []) == "data"
    assert classify_family("x", "", ["seo", "blog"]) == "docs-writing"
    assert classify_family("x", "", ["youtube", "video"]) == "media"
    assert classify_family("x", "", ["prompt-engineering"]) == "ai-agents"
    assert classify_family("x", "", ["code-review"]) == "backend"
    assert classify_family("mystery", "totally unclassifiable", []) == "misc"


# ── raw-URL upgrade for bare repo URLs ────────────────────────────────────────

def test_bare_github_repo_url_points_at_head_skill_md() -> None:
    assert _to_raw_url("https://github.com/owner/repo") == \
        "https://raw.githubusercontent.com/owner/repo/HEAD/SKILL.md"
    assert _to_raw_url("https://github.com/owner/repo/") == \
        "https://raw.githubusercontent.com/owner/repo/HEAD/SKILL.md"
    # /tree/ URLs keep the existing folder behaviour
    assert _to_raw_url("https://github.com/o/r/tree/main/skills/x") == \
        "https://raw.githubusercontent.com/o/r/main/skills/x/SKILL.md"


# ── sync end-to-end (hermetic) ───────────────────────────────────────────────

_ITEMS = [
    {"id": "gh/a/react-forms", "name": "React Forms", "source": "community",
     "description": "Build accessible React forms", "tags": ["react"],
     "url": "https://github.com/a/react-forms"},
    {"id": "gh/b/react-forms-dupe", "name": "React Forms", "source": "community",
     "description": "duplicate slug", "tags": ["react"],
     "url": "https://github.com/b/react-forms"},
    {"id": "curated/sql-tuning", "name": "SQL Tuning", "source": "curated",
     "description": "Tune slow queries", "tags": ["sql"], "url": None},
]


def _body_for(req) -> str:
    return (
        "---\n"
        f"name: {req.name}\n"
        f"description: {req.description}\n"
        "tags: []\n"
        "version: 1\n"
        "---\n\n## Instructions\n\nDo the thing.\n"
    )


@pytest.mark.asyncio
async def test_sync_organizes_dedupes_imports_and_reports(tmp_path) -> None:
    from backend.persistence.db import init_db
    from backend.skills import registry as regmod
    from backend.skills.registry import list_skills

    await init_db()
    fetched: list[str] = []

    async def fake_fetch(req):
        fetched.append(req.name)
        return _body_for(req)

    with patch("backend.registries.skills.search_skills",
               new=AsyncMock(return_value={"items": _ITEMS,
                                           "sources_failed": ["clawhub"]})), \
         patch("backend.api.install_http._fetch_skill_body", new=fake_fetch), \
         patch.object(regmod, "embed", side_effect=_fake_embed):
        report = await sync_skills(force_refresh=False)

    # Dedupe: the second "React Forms" slug was skipped.
    assert report["duplicates"] == 1
    assert sorted(report["new"]) == ["react-forms", "sql-tuning"]
    assert report["sources_failed"] == ["clawhub"]

    # On-disk family layout.
    root = regmod.SKILLS_ROOT
    assert (root / "frontend" / "react-forms" / "SKILL.md").exists()
    assert (root / "data" / "sql-tuning" / "SKILL.md").exists()
    assert report["families"] == {"frontend": 1, "data": 1}
    assert report["disk_bytes"] > 0

    # Registry import kept working — hybrid_search sees the library.
    ids = {s.id for s in await list_skills()}
    assert {"react-forms", "sql-tuning"} <= ids

    # Re-sync with identical content → unchanged, nothing refetched anew.
    with patch("backend.registries.skills.search_skills",
               new=AsyncMock(return_value={"items": _ITEMS,
                                           "sources_failed": []})), \
         patch("backend.api.install_http._fetch_skill_body", new=fake_fetch), \
         patch.object(regmod, "embed", side_effect=_fake_embed):
        again = await sync_skills(force_refresh=False)
    assert sorted(again["unchanged"]) == ["react-forms", "sql-tuning"]
    assert again["new"] == [] and again["updated"] == []


@pytest.mark.asyncio
async def test_sync_flags_failures_and_keeps_going(tmp_path) -> None:
    from backend.persistence.db import init_db
    from backend.skills import registry as regmod

    await init_db()

    async def flaky_fetch(req):
        if req.name == "React Forms":
            raise RuntimeError("upstream 404")
        return _body_for(req)

    with patch("backend.registries.skills.search_skills",
               new=AsyncMock(return_value={"items": _ITEMS[:1] + _ITEMS[2:],
                                           "sources_failed": []})), \
         patch("backend.api.install_http._fetch_skill_body", new=flaky_fetch), \
         patch.object(regmod, "embed", side_effect=_fake_embed):
        report = await sync_skills(force_refresh=False)

    assert [f["slug"] for f in report["failed"]] == ["react-forms"]
    assert "sql-tuning" in report["new"] + report["unchanged"]
