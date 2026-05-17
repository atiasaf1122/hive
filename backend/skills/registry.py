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

SKILLS_ROOT = Path.home() / ".hive" / "skills"

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


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def import_skill(path: Path, db_path: Path = DB_PATH) -> Skill:
    """Parse a SKILL.md, embed its description, and upsert into the skills table."""
    frontmatter, body = parse_skill_file(path)

    raw_name = frontmatter.get("name") or path.parent.name
    skill_id = _slugify(raw_name)
    name = frontmatter.get("name") or raw_name
    description = frontmatter.get("description", "").strip()
    tags: list[str] = frontmatter.get("tags") or []
    version = int(frontmatter.get("version", 1))

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
