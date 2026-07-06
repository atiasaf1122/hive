"""Lessons persistence + conservative retrieval (D1.1 / D1.4 / D1.5)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from backend.persistence.db import DB_PATH, get_conn
from backend.skills.embedder import cosine_similarity, deserialize, embed, serialize

logger = logging.getLogger(__name__)

# Similarity bar — err toward injecting NOTHING. Superficially similar
# lessons anchor agents on wrong fixes; unsafe injection is worse than none.
# E0.1.3 measurement (all-MiniLM, real lesson + realistic queries): the
# original 0.55 was unreachable in practice — a genuinely-similar task view
# scores ~0.37-0.60 while UNRELATED tasks land ≤0.15 (worst observed 0.30
# for git-adjacent wording, where the lesson is legitimately relevant).
# 0.35 with max-over-views keeps a real margin against unrelated injection
# while making the learning loop actually able to close.
RETRIEVAL_THRESHOLD = 0.35
MAX_LESSONS_PER_BRIEF = 3
# Archive after this many injections where the warned-about failure
# happened anyway.
ARCHIVE_AFTER_UNCONFIRMED = 3
# Staleness: not applied for this long → down-weighted in ranking.
# (Deviation from the spec's "30+ sessions": measured in days — sessions
# aren't a monotonic clock across projects; 30 days is the honest proxy.)
STALE_AFTER_DAYS = 30


@dataclass
class Lesson:
    id: int
    scope: str
    project_path: str | None
    title: str
    description: str
    content: str
    trigger_context: str
    origin: str
    source_session: str
    source_evidence: str
    times_applied: int
    times_confirmed: int
    times_unconfirmed: int
    last_applied_at: str | None
    status: str
    created_at: str


def _row_to_lesson(row) -> Lesson:
    return Lesson(
        id=row["id"], scope=row["scope"], project_path=row["project_path"],
        title=row["title"], description=row["description"], content=row["content"],
        trigger_context=row["trigger_context"], origin=row["origin"],
        source_session=row["source_session"], source_evidence=row["source_evidence"],
        times_applied=row["times_applied"], times_confirmed=row["times_confirmed"],
        times_unconfirmed=row["times_unconfirmed"],
        last_applied_at=row["last_applied_at"], status=row["status"],
        created_at=row["created_at"],
    )


async def save_lesson(
    *,
    scope: str,
    project_path: str | None,
    title: str,
    description: str,
    content: str,
    trigger_context: str,
    origin: str,
    source_session: str,
    source_evidence: str,
    db_path: Path = DB_PATH,
) -> int:
    vector = embed(f"{trigger_context}\n{content}")
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            """
            INSERT INTO lessons
                (scope, project_path, title, description, content,
                 trigger_context, origin, source_session, source_evidence, embedding)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (scope, project_path, title, description, content,
             trigger_context, origin, source_session, source_evidence,
             serialize(vector)),
        )
        await conn.commit()
        return int(cursor.lastrowid)


async def list_lessons(
    status: str | None = None, db_path: Path = DB_PATH
) -> list[Lesson]:
    async with get_conn(db_path) as conn:
        if status:
            cursor = await conn.execute(
                "SELECT * FROM lessons WHERE status=? ORDER BY created_at DESC", (status,))
        else:
            cursor = await conn.execute("SELECT * FROM lessons ORDER BY created_at DESC")
        rows = await cursor.fetchall()
    return [_row_to_lesson(r) for r in rows]


async def get_lesson(lesson_id: int, db_path: Path = DB_PATH) -> Lesson | None:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,))
        row = await cursor.fetchone()
    return _row_to_lesson(row) if row else None


async def set_lesson_status(lesson_id: int, status: str, db_path: Path = DB_PATH) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute("UPDATE lessons SET status=? WHERE id=?", (status, lesson_id))
        await conn.commit()


async def delete_lesson(lesson_id: int, db_path: Path = DB_PATH) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute("DELETE FROM lesson_applications WHERE lesson_id=?", (lesson_id,))
        await conn.execute("DELETE FROM lessons WHERE id=?", (lesson_id,))
        await conn.commit()


# ── D1.4 conservative retrieval ─────────────────────────────────────────────


async def retrieve_lessons(
    query: str,
    *,
    project_path: str | None,
    task: str | None = None,
    top_k: int = MAX_LESSONS_PER_BRIEF,
    threshold: float = RETRIEVAL_THRESHOLD,
    db_path: Path = DB_PATH,
) -> list[Lesson]:
    """Semantic match with a high bar. Empty result is normal and correct.

    Similarity is the MAX over separate views (agent subtask, session task)
    rather than one concatenated embedding — E0.1.3 measured that a
    composite query dilutes both signals below usefulness (similar 0.376
    vs unrelated 0.325), while per-view scores separate cleanly."""
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM lessons WHERE status='active' AND embedding IS NOT NULL")
        rows = await cursor.fetchall()
    if not rows:
        return []

    view_vecs = [embed(query)]
    if task and task.strip() and task.strip() != query.strip():
        view_vecs.append(embed(task))
    now = datetime.utcnow()
    scored: list[tuple[float, Lesson]] = []
    for row in rows:
        lesson_vec = deserialize(row["embedding"])
        sim = max(cosine_similarity(v, lesson_vec) for v in view_vecs)
        if sim < threshold:
            continue
        lesson = _row_to_lesson(row)
        rank = sim
        # Same-project lessons beat global ones.
        if lesson.scope == "project" and lesson.project_path == project_path:
            rank += 0.10
        elif lesson.scope == "project":
            continue  # someone else's project — never inject
        # Recently-confirmed beats stale.
        if lesson.times_confirmed > 0:
            rank += 0.05
        if lesson.last_applied_at:
            try:
                last = datetime.fromisoformat(lesson.last_applied_at)
                if now - last > timedelta(days=STALE_AFTER_DAYS):
                    rank -= 0.10
            except ValueError:
                pass
        scored.append((rank, lesson))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [lesson for _, lesson in scored[:top_k]]


def render_lessons_section(lessons: list[Lesson]) -> str:
    if not lessons:
        return ""
    lines = ["## Lessons from past sessions"]
    for lesson in lessons:
        lines.append(f"- **{lesson.title}**: {lesson.content}")
    return "\n".join(lines)


async def record_application(
    session_id: str, lesson_id: int, agent_id: str, db_path: Path = DB_PATH
) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute(
            "INSERT INTO lesson_applications (session_id, lesson_id, agent_id) VALUES (?,?,?)",
            (session_id, lesson_id, agent_id),
        )
        await conn.execute(
            "UPDATE lessons SET times_applied = times_applied + 1, "
            "last_applied_at = datetime('now') WHERE id=?",
            (lesson_id,),
        )
        await conn.commit()


# ── D1.5 hygiene ────────────────────────────────────────────────────────────


async def resolve_applications(
    session_id: str,
    failure_texts: list[str],
    *,
    recurrence_threshold: float = 0.50,
    db_path: Path = DB_PATH,
) -> dict[int, bool]:
    """Close the loop for every lesson injected this session.

    A lesson is CONFIRMED when nothing resembling its failure class occurred;
    it is NOT confirmed (and eventually archived) when a semantically-similar
    failure happened despite the injection. Returns {lesson_id: confirmed}.
    """
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT DISTINCT lesson_id FROM lesson_applications "
            "WHERE session_id=? AND resolved=0", (session_id,))
        lesson_ids = [r["lesson_id"] for r in await cursor.fetchall()]
    if not lesson_ids:
        return {}

    failure_vecs = [embed(t) for t in failure_texts if t.strip()]
    outcomes: dict[int, bool] = {}
    for lid in lesson_ids:
        lesson = await get_lesson(lid, db_path=db_path)
        if lesson is None:
            continue
        lesson_vec = embed(f"{lesson.trigger_context}\n{lesson.content}")
        recurred = any(
            cosine_similarity(lesson_vec, fv) >= recurrence_threshold
            for fv in failure_vecs
        )
        outcomes[lid] = not recurred
        async with get_conn(db_path) as conn:
            if recurred:
                await conn.execute(
                    "UPDATE lessons SET times_unconfirmed = times_unconfirmed + 1 "
                    "WHERE id=?", (lid,))
                cursor = await conn.execute(
                    "SELECT times_unconfirmed FROM lessons WHERE id=?", (lid,))
                row = await cursor.fetchone()
                if row and row["times_unconfirmed"] >= ARCHIVE_AFTER_UNCONFIRMED:
                    await conn.execute(
                        "UPDATE lessons SET status='archived' WHERE id=?", (lid,))
                    logger.warning("Lesson %d archived — failed to prevent its failure class %d times",
                                   lid, row["times_unconfirmed"])
            else:
                await conn.execute(
                    "UPDATE lessons SET times_confirmed = times_confirmed + 1 "
                    "WHERE id=?", (lid,))
            await conn.execute(
                "UPDATE lesson_applications SET resolved=1 "
                "WHERE session_id=? AND lesson_id=?", (session_id, lid))
            await conn.commit()
    return outcomes
