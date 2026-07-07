"""Local skills library sync (post-1.0 Part 4).

Decision: own the whole skills library locally — skills are tiny text
files, and the online sources (clawhub/cookbook/community) are flaky.
`sync_skills` runs discovery once, downloads every discoverable skill
into ~/.hive/skills/<family>/<slug>/SKILL.md, imports each into the
registry the orchestrator already hybrid-searches, and reports the diff.
Manual only (`hive skills sync` / the Skills page Sync button) — no
background syncing.

Families are derived from tags/name/description with an ordered keyword
table; anything unmatched lands in misc/.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

FAMILIES = ("frontend", "backend", "devops", "data", "docs-writing",
            "media", "ai-agents", "misc")

# Ordered: first family whose keywords intersect the item's tag/name/desc
# haystack wins. Extend by adding keywords.
_FAMILY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("frontend", ("frontend", "react", "vue", "svelte", "css", "tailwind",
                  "html", "ui", "design-system", "component", "animation",
                  "svg", "web-design", "slides", "presentation")),
    ("devops", ("devops", "docker", "kubernetes", "ci", "cicd", "deploy",
                "terraform", "infrastructure", "github-actions", "shell",
                "monitoring", "nginx")),
    ("data", ("data", "sql", "database", "postgres", "sqlite", "pandas",
              "etl", "analytics", "spreadsheet", "xlsx", "csv",
              "visualization", "chart")),
    ("docs-writing", ("docs", "documentation", "writing", "blog", "seo",
                      "content", "readme", "technical-writing", "markdown",
                      "note-taking", "knowledge-management", "translation")),
    ("media", ("media", "image", "video", "audio", "youtube", "podcast",
               "image-generation", "photo", "music", "logo", "pdf",
               "pptx", "social-cards")),
    ("ai-agents", ("ai-agent", "ai-agents", "agent-skill", "agents", "llm",
                   "prompt", "prompt-engineering", "mcp", "rag",
                   "orchestration", "automation")),
    ("backend", ("backend", "api", "python", "rust", "golang", "node",
                 "typescript", "testing", "refactor", "debugging", "security",
                 "code-review", "architecture", "cli")),
]


def classify_family(name: str, description: str, tags: list[str]) -> str:
    haystack = " ".join([name or "", description or "", *(tags or [])]).lower()
    for family, keywords in _FAMILY_RULES:
        if any(kw in haystack for kw in keywords):
            return family
    return "misc"


async def sync_skills(force_refresh: bool = True, concurrency: int = 8) -> dict:
    """Discover → download → organize → import. Returns a diff report."""
    from backend.api.install_http import SkillInstallRequest, _fetch_skill_body
    from backend.registries.skills import search_skills
    from backend.skills.registry import SKILLS_ROOT, _slugify, import_skill

    res = await search_skills(query=None, source="all",
                              db_force_refresh=force_refresh)
    items = res.get("items") or []

    # Dedupe by slug — first occurrence wins (post-processing already ranks
    # verified/curated entries first).
    seen: set[str] = set()
    unique: list[dict] = []
    duplicates = 0
    for item in items:
        slug = _slugify(str(item.get("name") or item.get("id") or ""))
        if not slug or slug in seen:
            duplicates += 1
            continue
        seen.add(slug)
        unique.append({**item, "_slug": slug})

    report: dict = {
        "discovered": len(items), "duplicates": duplicates,
        "sources_failed": res.get("sources_failed") or [],
        "new": [], "updated": [], "unchanged": [],
        "synthesized": [], "failed": [], "families": {},
    }
    sem = asyncio.Semaphore(concurrency)

    async def _one(item: dict) -> None:
        slug = item["_slug"]
        family = classify_family(item.get("name") or "",
                                 item.get("description") or "",
                                 item.get("tags") or [])
        target_dir = SKILLS_ROOT / family / slug
        target = target_dir / "SKILL.md"
        req = SkillInstallRequest(
            id=str(item.get("id") or slug), name=str(item.get("name") or slug),
            description=str(item.get("description") or ""),
            source=str(item.get("source") or "sync"),
            url=item.get("url"), tags=list(item.get("tags") or []),
        )
        try:
            async with sem:
                body = await _fetch_skill_body(req)
        except Exception as exc:  # noqa: BLE001 — skip, flag, keep going
            report["failed"].append({"slug": slug, "error": str(exc)[:200]})
            return

        # _fetch_skill_body degrades to a synthesized SKILL.md when the
        # upstream file is unreachable — flag those so the report is honest.
        if f"_Installed from {req.source}" in body:
            report["synthesized"].append(slug)

        old = target.read_text(encoding="utf-8") if target.exists() else None
        if old == body:
            report["unchanged"].append(slug)
            report["families"][family] = report["families"].get(family, 0) + 1
            return

        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        try:
            await import_skill(target)
        except Exception as exc:  # noqa: BLE001 — invalid frontmatter etc.
            target.unlink(missing_ok=True)
            report["failed"].append({"slug": slug, "error": str(exc)[:200]})
            return

        # Relocate: a pre-sync flat install (~/.hive/skills/<slug>/) is now
        # superseded by the family path the registry points at.
        flat = SKILLS_ROOT / slug
        if flat != target_dir and (flat / "SKILL.md").exists():
            shutil.rmtree(flat, ignore_errors=True)

        report["new" if old is None else "updated"].append(slug)
        report["families"][family] = report["families"].get(family, 0) + 1

    await asyncio.gather(*(_one(item) for item in unique))

    report["total_in_library"] = sum(report["families"].values())
    report["disk_bytes"] = _tree_size(SKILLS_ROOT)
    return report


def _tree_size(root: Path) -> int:
    try:
        return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    except OSError:
        return 0
