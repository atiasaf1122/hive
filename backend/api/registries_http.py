"""HTTP routes for the public registry proxies.

Endpoints (cached server-side for 1 h):

    GET /api/registries/mcp/list?q=&source=all|official|smithery&category=
        → {items, fallback, sources_tried, sources_failed, categories, ...}

The old GET /skills/search proxy was removed in the final close-out: the
Skills page browses the LOCAL library and online skill discovery happens
only inside `hive skills sync` / POST /skills/sync (which call
backend.registries.skills.search_skills directly). `GET /diagnose` stays
as the connectivity doctor for both fetcher families.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from backend.registries import mcp, skills

router = APIRouter(prefix="/api/registries")


@router.get("/mcp/list")
async def mcp_list(
    q: str | None = Query(None, max_length=120),
    source: str = Query("all"),
    category: str | None = Query(None),
    force_refresh: bool = Query(False),
) -> dict:
    return await mcp.list_mcp_servers(
        query=q, source=source, category=category, force_refresh=force_refresh
    )


@router.get("/diagnose")
async def diagnose_all() -> dict:
    """Fire every fetcher fresh and report per-source status. Used by
    Settings → Integrations → "Test registry connections"."""
    return {
        "skills": await skills.diagnose(),
        "mcp": await mcp.diagnose(),
    }
