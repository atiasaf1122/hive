"""MCP catalog HTTP surface (Phase C).

    GET /api/mcp/catalog — the curated runnable server set + per-server
                           preflight status (requirements met / missing)
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from backend.mcp.catalog import list_specs, preflight

router = APIRouter(prefix="/api/mcp")


@router.get("/catalog")
async def get_catalog() -> dict:
    items = []
    for spec in list_specs():
        missing = preflight(spec)
        d = asdict(spec)
        # Never leak resolved secrets — headers/env hold ${VAR} templates
        # only, which is what we want the UI to show.
        d["preflight_ok"] = not missing
        d["missing"] = missing
        items.append(d)
    return {"servers": items}
