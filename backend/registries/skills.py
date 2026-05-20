"""Skills registry proxy — three sources with real diagnostics.

Sources:
  - ClawHub                     https://clawhub.dev/api/skills/search
  - Anthropic Cookbook (GitHub) anthropics/anthropic-cookbook/contents/skills
  - Community GitHub topic      topic:claude-skill (search-by-topic)

Every fetcher returns a `FetchResult` with the actual error string when
it fails — so `/api/registries/skills/search` and `/diagnose` can tell
the UI exactly what went wrong (DNS, 403 rate-limited, parse error, …)
instead of the previous generic "couldn't reach clawhub" string.

GitHub API: we send ``Authorization: Bearer $GITHUB_TOKEN`` when the env
var is set — unauthenticated GitHub rate-limits at 60 req/h which the
user exhausts in normal browsing. With a PAT it's 5 000 req/h.
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
from backend.registries.curated import CURATED_SKILLS

logger = logging.getLogger(__name__)

_CACHE = TtlCache(ttl_seconds=3600.0)
_USER_AGENT = "HIVE-desktop/9C (+https://github.com/anthropics/claude-code)"

_TRUSTED_PUBLISHERS = {"anthropic", "anthropic-cookbook", "anthropics"}
_AUTO_INSTALL_STAR_FLOOR = 100

# Per-source diagnostics: last attempt outcome + last successful timestamp.
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


async def search_skills(
    query: str | None = None,
    source: str = "all",
    db_force_refresh: bool = False,
) -> dict[str, Any]:
    key = f"skills:{source}:{(query or '').lower()}"
    if not db_force_refresh:
        cached = _CACHE.get(key)
        if cached is not None:
            return {**cached, "cached_at_age_seconds": _CACHE.age_seconds(key)}

    fetchers = {
        "clawhub": _fetch_clawhub,
        "cookbook": _fetch_cookbook,
        "community": _fetch_community,
    }
    if source != "all":
        fetchers = {source: fetchers[source]} if source in fetchers else {}

    results: list[FetchResult] = await asyncio.gather(
        *[_run_fetcher(name, fn, query) for name, fn in fetchers.items()]
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
        items = _filter_curated_by_source(source)
        fallback = True
    else:
        ids = {i["id"] for i in items}
        for c in _filter_curated_by_source(source):
            if c["id"] not in ids:
                items.append(c)
        fallback = False

    if query:
        q = query.lower()
        items = [
            i for i in items
            if q in i["name"].lower()
            or q in i["description"].lower()
            or any(q in str(t).lower() for t in (i.get("tags") or []))
        ]

    items = _post_process_items(items)
    payload = {
        "items": items,
        "fallback": fallback,
        "sources_tried": tried,
        "sources_failed": failed,
        "per_source": per_source,
    }
    _CACHE.set(key, payload)
    return {**payload, "cached_at_age_seconds": 0.0}


async def diagnose() -> dict:
    """Run every fetcher fresh and return per-source status — for the
    Settings → Integrations → "Test registry connections" button."""
    fetchers = {
        "clawhub": _fetch_clawhub,
        "cookbook": _fetch_cookbook,
        "community": _fetch_community,
    }
    results: list[FetchResult] = await asyncio.gather(
        *[_run_fetcher(name, fn, None) for name, fn in fetchers.items()]
    )
    return {
        "github_token_present": bool(os.environ.get("GITHUB_TOKEN")),
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


async def _run_fetcher(name: str, fn, query: str | None) -> FetchResult:
    start = time.time()
    msg: str | None = None
    try:
        items = await fn(query)
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
        logger.warning("skill registry %s failed: %s", name, msg)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        msg = f"network error: {exc}"
        logger.warning("skill registry %s unreachable: %s", name, exc)
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("skill registry %s crashed", name)

    elapsed = int((time.time() - start) * 1000)
    result = FetchResult(
        source=name, ok=False, items=[], error=msg or "unknown error",
        duration_ms=elapsed, fetched_at=time.time(),
    )
    _LAST_OUTCOME[name] = result
    return result


async def _fetch_clawhub(query: str | None) -> list[dict]:
    params: dict[str, str] = {}
    if query:
        params["q"] = query
    async with httpx.AsyncClient(timeout=5.0, headers={"User-Agent": _USER_AGENT}) as client:
        resp = await client.get("https://clawhub.dev/api/skills/search", params=params)
        resp.raise_for_status()
        data = resp.json()

    items: list[dict] = []
    for raw in data.get("skills", [])[:50]:
        stars = int(raw.get("stars") or 0)
        author = (raw.get("author") or "").lower()
        verified = bool(raw.get("verified") or author in _TRUSTED_PUBLISHERS)
        items.append({
            "id": f"clawhub/{raw.get('id') or raw.get('slug') or raw.get('name')}",
            "name": raw.get("name", "unknown"),
            "description": raw.get("description", ""),
            "source": "clawhub",
            "source_label": "ClawHub",
            "url": raw.get("url") or f"https://clawhub.dev/skills/{raw.get('slug', '')}",
            "tags": raw.get("tags") or [],
            "stars": stars,
            "downloads": raw.get("downloads"),
            "verified": verified,
            "author": raw.get("author"),
        })
    return items


async def _fetch_cookbook(query: str | None) -> list[dict]:
    url = "https://api.github.com/repos/anthropics/anthropic-cookbook/contents/skills"
    async with httpx.AsyncClient(timeout=8.0, headers=_gh_headers()) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    items: list[dict] = []
    for entry in data:
        if entry.get("type") != "dir":
            continue
        name = entry.get("name", "")
        items.append({
            "id": f"cookbook/{name}",
            "name": name,
            "description": f"Cookbook skill: {name}",
            "source": "cookbook",
            "source_label": "Anthropic Cookbook",
            "url": entry.get("html_url"),
            "tags": [],
            "stars": None,
            "downloads": None,
            "verified": True,
        })
    if query:
        q = query.lower()
        items = [i for i in items if q in i["name"].lower()]
    return items


async def _fetch_community(query: str | None) -> list[dict]:
    q = "topic:claude-skill"
    if query:
        q += f" {query}"
    url = "https://api.github.com/search/repositories"
    async with httpx.AsyncClient(timeout=8.0, headers=_gh_headers()) as client:
        resp = await client.get(url, params={"q": q, "sort": "stars", "per_page": "30"})
        resp.raise_for_status()
        data = resp.json()

    items: list[dict] = []
    for r in data.get("items", []):
        stars = int(r.get("stargazers_count") or 0)
        items.append({
            "id": f"github/{r['full_name']}",
            "name": r.get("name", "unknown"),
            "description": (r.get("description") or "")[:200],
            "source": "community",
            "source_label": "GitHub",
            "url": r.get("html_url"),
            "tags": r.get("topics", []),
            "stars": stars,
            "downloads": None,
            "verified": stars >= _AUTO_INSTALL_STAR_FLOOR,
            "author": (r.get("owner") or {}).get("login"),
        })
    return items


def _filter_curated_by_source(source: str) -> list[dict]:
    if source == "all":
        return list(CURATED_SKILLS)
    return [s for s in CURATED_SKILLS if s["source"] == source]


def _post_process_items(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        verified = bool(it.get("verified"))
        stars = it.get("stars") or 0
        auto_install_ok = verified or (isinstance(stars, int) and stars >= _AUTO_INSTALL_STAR_FLOOR)
        out.append({
            **it,
            "auto_install_ok": auto_install_ok,
            "warn_unverified": not verified,
        })
    return out
