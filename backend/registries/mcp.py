"""MCP plugin registry proxy with per-source diagnostics.

Sources:
  - Official MCP Registry        https://registry.modelcontextprotocol.io
  - Smithery                     https://smithery.ai/api/servers
  - Awesome MCP (GitHub README)  github.com/punkpeye/awesome-mcp-servers

Same pattern as `skills.py`: each fetcher returns a `FetchResult` carrying
the real error string. On failure we transparently fall through to the
curated set so the UI always renders something useful.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from backend.registries.cache import TtlCache
from backend.registries.curated import CURATED_MCP_SERVERS

logger = logging.getLogger(__name__)

_CACHE = TtlCache(ttl_seconds=3600.0)
_USER_AGENT = "HIVE-desktop/9C MCP-proxy"

_LAST_OUTCOME: dict[str, "FetchResult"] = {}
_LAST_SUCCESS_AT: dict[str, float] = {}


@dataclass
class FetchResult:
    source: str
    ok: bool
    items: list[dict]
    error: str | None
    duration_ms: int
    fetched_at: float


def _gh_headers() -> dict[str, str]:
    h = {"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def list_mcp_servers(
    query: str | None = None,
    source: str = "all",
    category: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    key = f"mcp:{source}:{category or ''}:{(query or '').lower()}"
    if not force_refresh:
        cached = _CACHE.get(key)
        if cached is not None:
            return {**cached, "cached_at_age_seconds": _CACHE.age_seconds(key)}

    fetchers = {
        "official": _fetch_official,
        "smithery": _fetch_smithery,
        "awesome": _fetch_awesome,
    }
    if source != "all":
        fetchers = {source: fetchers[source]} if source in fetchers else {}

    results: list[FetchResult] = await asyncio.gather(
        *[_run_fetcher(name, fn) for name, fn in fetchers.items()]
    )

    items: list[dict] = []
    tried: list[str] = []
    failed: list[str] = []
    per_source: dict[str, dict] = {}

    for res in results:
        tried.append(res.source)
        per_source[res.source] = {
            "ok": res.ok,
            "error": res.error,
            "duration_ms": res.duration_ms,
            "last_success_at": _LAST_SUCCESS_AT.get(res.source),
        }
        if res.ok:
            items.extend(res.items)
        else:
            failed.append(res.source)

    if not items or len(failed) == len(tried):
        items = _filter_curated(source)
        fallback = True
    else:
        ids = {i["id"] for i in items}
        for c in _filter_curated(source):
            if c["id"] not in ids:
                items.append(c)
        fallback = False

    if query:
        q = query.lower()
        items = [
            i for i in items
            if q in i["name"].lower() or q in i["description"].lower()
        ]
    if category and category != "all":
        items = [i for i in items if i.get("category") == category]

    payload = {
        "items": items,
        "fallback": fallback,
        "sources_tried": tried,
        "sources_failed": failed,
        "categories": _collect_categories(items),
        "per_source": per_source,
    }
    _CACHE.set(key, payload)
    return {**payload, "cached_at_age_seconds": 0.0}


async def diagnose() -> dict:
    fetchers = {
        "official": _fetch_official,
        "smithery": _fetch_smithery,
        "awesome": _fetch_awesome,
    }
    results: list[FetchResult] = await asyncio.gather(
        *[_run_fetcher(name, fn) for name, fn in fetchers.items()]
    )
    return {
        "sources": [
            {
                "name": r.source,
                "ok": r.ok,
                "error": r.error,
                "items_returned": len(r.items),
                "duration_ms": r.duration_ms,
                "last_success_at": _LAST_SUCCESS_AT.get(r.source),
            }
            for r in results
        ],
    }


async def _run_fetcher(name: str, fn) -> FetchResult:
    start = time.time()
    msg: str | None = None
    try:
        items = await fn()
        elapsed = int((time.time() - start) * 1000)
        result = FetchResult(
            source=name, ok=True, items=items, error=None,
            duration_ms=elapsed, fetched_at=time.time(),
        )
        _LAST_OUTCOME[name] = result
        _LAST_SUCCESS_AT[name] = time.time()
        return result
    except httpx.HTTPStatusError as exc:
        msg = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        logger.warning("MCP registry %s failed: %s", name, msg)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        msg = f"network error: {exc}"
        logger.warning("MCP registry %s unreachable: %s", name, exc)
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("MCP registry %s crashed", name)

    elapsed = int((time.time() - start) * 1000)
    result = FetchResult(
        source=name, ok=False, items=[], error=msg or "unknown error",
        duration_ms=elapsed, fetched_at=time.time(),
    )
    _LAST_OUTCOME[name] = result
    return result


async def _fetch_official() -> list[dict]:
    async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": _USER_AGENT}) as client:
        resp = await client.get("https://registry.modelcontextprotocol.io/api/servers")
        resp.raise_for_status()
        data = resp.json()

    items: list[dict] = []
    for r in data.get("servers", []):
        items.append({
            "id": f"mcp/{r.get('id') or r.get('name')}",
            "name": r.get("name", "unknown"),
            "description": r.get("description", ""),
            "source": "official",
            "source_label": "MCP Registry",
            "install": r.get("install") or {"transport": "npm", "package": r.get("package", "")},
            "category": r.get("category", "other"),
            "permissions": r.get("permissions", []),
            "homepage": r.get("homepage"),
            "verified": True,
            "installs": r.get("installs"),
        })
    return items


async def _fetch_smithery() -> list[dict]:
    async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": _USER_AGENT}) as client:
        resp = await client.get("https://smithery.ai/api/servers")
        resp.raise_for_status()
        data = resp.json()

    items: list[dict] = []
    for r in data.get("servers", []):
        items.append({
            "id": f"smithery/{r.get('slug') or r.get('id')}",
            "name": r.get("name", "unknown"),
            "description": r.get("description", ""),
            "source": "smithery",
            "source_label": "Smithery",
            "install": {
                "transport": r.get("transport", "smithery"),
                "package": r.get("package", ""),
            },
            "category": r.get("category", "other"),
            "permissions": r.get("permissions", []),
            "homepage": r.get("homepage"),
            "verified": bool(r.get("verified")),
            "installs": r.get("installs"),
        })
    return items


async def _fetch_awesome() -> list[dict]:
    """awesome-mcp-servers is a hand-maintained README. Parsing it live is
    fragile (the format drifts), so we just touch the README to confirm
    reachability for diagnostics, and rely on the curated mirror in
    `curated.py` for content."""
    url = "https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md"
    async with httpx.AsyncClient(timeout=5.0, headers=_gh_headers()) as client:
        resp = await client.head(url)
        resp.raise_for_status()
    return []


def _filter_curated(source: str) -> list[dict]:
    if source == "all":
        return list(CURATED_MCP_SERVERS)
    return [s for s in CURATED_MCP_SERVERS if s["source"] == source]


def _collect_categories(items: list[dict]) -> list[str]:
    seen = set()
    out: list[str] = []
    for it in items:
        cat = it.get("category")
        if cat and cat not in seen:
            seen.add(cat)
            out.append(cat)
    return sorted(out)
