"""Skills registry — import, store, and semantically search skills."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from backend.persistence.db import DB_PATH, get_conn
from backend.skills.embedder import cosine_similarity, deserialize, embed, serialize

logger = logging.getLogger(__name__)

from backend.persistence.db import HIVE_DIR

SKILLS_ROOT = HIVE_DIR / "skills"

_SKILL_TEMPLATE = """\
---
name: {name}
description: {description}
tags: {tags_json}
version: 1
---

## Instructions

Describe what this skill does and how agents should apply it.

## Examples

Provide examples of tasks where this skill is relevant.
"""


@dataclass
class Skill:
    id: str
    name: str
    description: str
    tags: list[str]
    path: str
    instructions: str
    version: int = 1


# ── file parsing ──────────────────────────────────────────────────────────────

def parse_skill_file(path: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, markdown_body) from a SKILL.md file."""
    content = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n?(.*)", content, re.DOTALL)
    if not match:
        raise ValueError(
            f"{path} must start with YAML frontmatter (--- ... ---). "
            "Run 'hive skills create' to generate a valid template."
        )
    frontmatter = yaml.safe_load(match.group(1)) or {}
    body = match.group(2).strip()
    return frontmatter, body


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", text.lower()).strip("-")


def _parse_version(raw: object) -> int:
    """Lenient version parse — community SKILL.md files use semver strings
    ('1.2.0'); we keep the integer major so import never dies on it."""
    m = re.match(r"\d+", str(raw))
    return int(m.group(0)) if m else 1


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def import_skill(path: Path, db_path: Path = DB_PATH) -> Skill:
    """Parse a SKILL.md, embed its description, and upsert into the skills table."""
    frontmatter, body = parse_skill_file(path)

    raw_name = frontmatter.get("name") or path.parent.name
    skill_id = _slugify(raw_name)
    name = frontmatter.get("name") or raw_name
    description = frontmatter.get("description", "").strip()
    tags: list[str] = frontmatter.get("tags") or []
    version = _parse_version(frontmatter.get("version", 1))

    if not description:
        raise ValueError(f"{path}: 'description' is required in frontmatter.")

    embedding = embed(description)

    async with get_conn(db_path) as conn:
        await conn.execute(
            """INSERT OR REPLACE INTO skills
               (id, name, description, tags, path, instructions, embedding, version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (skill_id, name, description, json.dumps(tags),
             str(path), body, serialize(embedding), version),
        )
        await conn.commit()

    logger.info("Imported skill '%s' from %s", skill_id, path)
    return Skill(id=skill_id, name=name, description=description,
                 tags=tags, path=str(path), instructions=body, version=version)


async def create_skill_file(
    name: str,
    description: str,
    tags: list[str] | None = None,
    skills_root: Path = SKILLS_ROOT,
) -> Path:
    """Write a SKILL.md template to ~/.hive/skills/<slug>/SKILL.md."""
    slug = _slugify(name)
    skill_dir = skills_root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        _SKILL_TEMPLATE.format(
            name=name,
            description=description,
            tags_json=json.dumps(tags or []),
        ),
        encoding="utf-8",
    )
    return skill_path


async def list_skills(db_path: Path = DB_PATH) -> list[Skill]:
    """Return all registered skills ordered by name."""
    async with get_conn(db_path) as conn:
        async with conn.execute(
            "SELECT id, name, description, tags, path, instructions, version "
            "FROM skills ORDER BY name"
        ) as cur:
            rows = await cur.fetchall()

    return [
        Skill(
            id=row["id"], name=row["name"], description=row["description"],
            tags=json.loads(row["tags"] or "[]"), path=row["path"],
            instructions=row["instructions"], version=row["version"],
        )
        for row in rows
    ]


async def get_skill(skill_id: str, db_path: Path = DB_PATH) -> Skill | None:
    """Fetch a single skill by its slug ID."""
    async with get_conn(db_path) as conn:
        async with conn.execute(
            "SELECT id, name, description, tags, path, instructions, version "
            "FROM skills WHERE id = ?",
            (skill_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return Skill(
        id=row["id"], name=row["name"], description=row["description"],
        tags=json.loads(row["tags"] or "[]"), path=row["path"],
        instructions=row["instructions"], version=row["version"],
    )


async def delete_skill(skill_id: str, db_path: Path = DB_PATH) -> bool:
    """Remove a skill from the registry. Returns True if it existed."""
    async with get_conn(db_path) as conn:
        cur = await conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        await conn.commit()
        return cur.rowcount > 0


# ── semantic search ───────────────────────────────────────────────────────────

async def search(
    query: str,
    top_k: int = 3,
    threshold: float = 0.3,
    db_path: Path = DB_PATH,
) -> list[Skill]:
    """Return the top-K skills most relevant to query (above similarity threshold)."""
    query_vec = embed(query)

    async with get_conn(db_path) as conn:
        async with conn.execute(
            "SELECT id, name, description, tags, path, instructions, version, embedding "
            "FROM skills"
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return []

    scored: list[tuple[float, Skill]] = []
    for row in rows:
        if not row["embedding"]:
            continue
        skill_vec = deserialize(row["embedding"])
        score = cosine_similarity(query_vec, skill_vec)
        if score >= threshold:
            scored.append((
                score,
                Skill(
                    id=row["id"], name=row["name"], description=row["description"],
                    tags=json.loads(row["tags"] or "[]"), path=row["path"],
                    instructions=row["instructions"], version=row["version"],
                ),
            ))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [skill for _, skill in scored[:top_k]]


# ── Hybrid search (Section 7) ───────────────────────────────────────────────


@dataclass
class HybridHit:
    """A skill + the score breakdown the UI shows next to it."""
    skill: Skill
    semantic: float       # 0..1 cosine similarity
    keyword: float        # 0..1 BM25 normalised
    tag_match: float      # 0..1 fraction of provided tags this skill carries
    combined: float       # weighted sum used for the final ranking


async def hybrid_search(
    query: str,
    *,
    tags: list[str] | None = None,
    top_k: int = 10,
    threshold: float = 0.10,
    weights: tuple[float, float, float] = (0.4, 0.4, 0.2),
    db_path: Path = DB_PATH,
) -> list[HybridHit]:
    """Semantic + BM25 + tag-overlap hybrid ranking.

    Weights are (semantic, keyword, tag_overlap) and should sum to 1.0;
    the default `0.4/0.4/0.2` mirrors the layout in the v1.0 plan. Tags
    pre-filter when supplied: a skill must carry at least one matching
    tag to even be scored, OR all skills if no tags are supplied.
    """
    from backend.skills.bm25 import BM25, normalise

    async with get_conn(db_path) as conn:
        async with conn.execute(
            "SELECT id, name, description, tags, path, instructions, version, embedding "
            "FROM skills"
        ) as cur:
            rows = await cur.fetchall()
    if not rows:
        return []

    # 1. Optional tag filter — a hard gate, not a score component.
    tag_filter = {t.lower() for t in (tags or []) if t}
    candidates = []
    for row in rows:
        row_tags = [t.lower() for t in json.loads(row["tags"] or "[]")]
        if tag_filter and not (tag_filter & set(row_tags)):
            continue
        candidates.append((row, row_tags))
    if not candidates:
        return []

    # 2. Semantic score.
    query_vec = embed(query)
    sem_scores: list[float] = []
    for row, _ in candidates:
        if not row["embedding"]:
            sem_scores.append(0.0)
            continue
        sem_scores.append(cosine_similarity(query_vec, deserialize(row["embedding"])))

    # 3. BM25 over name + description + tags joined per skill.
    bm25 = BM25()
    bm25.fit(
        f"{row['name']} {row['description']} {' '.join(rt)}"
        for row, rt in candidates
    )
    kw_scores = normalise(bm25.score(query))

    # 4. Tag-overlap (Jaccard) when tags supplied; 0 otherwise.
    tag_scores: list[float] = []
    for _, row_tags in candidates:
        if not tag_filter:
            tag_scores.append(0.0)
        else:
            intersect = len(tag_filter & set(row_tags))
            union = len(tag_filter | set(row_tags))
            tag_scores.append(intersect / union if union else 0.0)

    w_sem, w_kw, w_tag = weights
    hits: list[HybridHit] = []
    for (row, _), s, k, t in zip(candidates, sem_scores, kw_scores, tag_scores):
        combined = w_sem * s + w_kw * k + w_tag * t
        if combined < threshold:
            continue
        hits.append(HybridHit(
            skill=Skill(
                id=row["id"], name=row["name"], description=row["description"],
                tags=json.loads(row["tags"] or "[]"), path=row["path"],
                instructions=row["instructions"], version=row["version"],
            ),
            semantic=round(s, 3), keyword=round(k, 3),
            tag_match=round(t, 3), combined=round(combined, 3),
        ))

    hits.sort(key=lambda h: h.combined, reverse=True)
    return hits[:top_k]


# ── LLM rerank gate (Section 7) ─────────────────────────────────────────────


@dataclass
class RerankResult:
    hits: list[HybridHit]
    used_llm: bool
    skipped_reason: str = ""


def should_use_rerank(
    *,
    expected_agent_count: int,
    tech_stack_complete: bool,
    ambiguous_query: bool,
) -> bool:
    """The "smart switch" — only spend Haiku tokens when the cheaper
    hybrid ranking probably isn't enough.

    Triggers when:
      - we're spawning many agents (≥5 — more cost-per-decision), OR
      - the user hasn't told us the tech stack, OR
      - the query has fewer than 4 informative tokens (ambiguous).
    """
    return (
        expected_agent_count >= 5
        or not tech_stack_complete
        or ambiguous_query
    )


async def maybe_rerank(
    hits: list[HybridHit],
    *,
    query: str,
    tech_stack: dict | None = None,
    expected_agent_count: int = 1,
    haiku_caller=None,
) -> RerankResult:
    """Optionally re-rank the hybrid hits with a Haiku call.

    `haiku_caller` is injected so we can wire the actual LLM in a follow-up
    pass. With it `None`, this returns the hits unchanged with
    `used_llm=False` so the caller can ship the cheap ranking now.
    """
    ambiguous = len([t for t in query.split() if len(t) > 2]) < 4
    tech_complete = bool(tech_stack and tech_stack.get("language"))
    if not should_use_rerank(
        expected_agent_count=expected_agent_count,
        tech_stack_complete=tech_complete,
        ambiguous_query=ambiguous,
    ):
        return RerankResult(hits=hits, used_llm=False,
                            skipped_reason="not triggered (small team, clear stack)")
    if haiku_caller is None:
        return RerankResult(hits=hits, used_llm=False,
                            skipped_reason="not_wired")

    prompt = _build_rerank_prompt(hits, query, tech_stack)
    try:
        raw = await haiku_caller(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Haiku rerank failed: %s", exc)
        return RerankResult(hits=hits, used_llm=False,
                            skipped_reason=f"haiku_error: {exc}")

    keep_ids = _parse_rerank_response(raw, [h.skill.id for h in hits])
    filtered = [h for h in hits if h.skill.id in keep_ids] or hits
    return RerankResult(hits=filtered, used_llm=True)


def _build_rerank_prompt(hits: list[HybridHit], query: str, tech_stack: dict | None) -> str:
    stack = ", ".join(f"{k}={v}" for k, v in (tech_stack or {}).items()) or "(none)"
    items = "\n".join(
        f"  - id={h.skill.id} · {h.skill.name}: {h.skill.description}"
        for h in hits
    )
    return (
        f"Task: {query}\n"
        f"Tech stack: {stack}\n\n"
        f"Candidate skills:\n{items}\n\n"
        f"Return the IDs of the skills that are actually relevant, one per "
        f"line. Drop anything that's only tangentially related."
    )


def _parse_rerank_response(raw: str, valid_ids: list[str]) -> set[str]:
    valid = set(valid_ids)
    out: set[str] = set()
    for line in (raw or "").splitlines():
        token = line.strip().lstrip("- ").strip()
        if token in valid:
            out.add(token)
    return out
