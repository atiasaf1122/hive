"""HTTP routes for the public registry proxies.

Endpoints (all cached server-side for 1 h):

    GET /api/registries/skills/search?q=&source=all|clawhub|cookbook|community
    GET /api/registries/mcp/list?q=&source=all|official|smithery|awesome&category=

Both return a uniform envelope:
    {items, fallback, sources_tried, sources_failed, cached_at_age_seconds, ...}

The frontend is responsible for showing the user warnings on items with
`warn_unverified=true` (skills) or with explicit `permissions`/non-trusted
source (plugins). The backend never auto-installs anything.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from backend.registries import mcp, skills

router = APIRouter(prefix="/api/registries")


@router.get("/skills/search")
async def skills_search(
    q: str | None = Query(None, max_length=120),
    source: str = Query("all"),
    force_refresh: bool = Query(False),
) -> dict:
    return await skills.search_skills(query=q, source=source, db_force_refresh=force_refresh)


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
