"""MCP catalog HTTP surface (Phase C).

    GET /api/mcp/catalog — the curated runnable server set + per-server
                           preflight status (requirements met / missing)
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from backend.mcp.catalog import list_specs, preflight

router = APIRouter(prefix="/api/mcp")


@router.get("/doctor")
async def run_doctor() -> dict:
    """D0.3: live-check every catalog server (spawn + initialize handshake).

    Bypasses the cache — this endpoint exists to answer "is it broken NOW".
    """
    from backend.mcp.doctor import check_server

    results = []
    for spec in list_specs():
        static_missing = preflight(spec)
        if static_missing:
            results.append({
                "id": spec.id, "ok": False,
                "detail": "; ".join(static_missing),
            })
            continue
        ok, detail = await check_server(spec, use_cache=False)
        results.append({"id": spec.id, "ok": ok, "detail": detail})
    return {"servers": results}


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
