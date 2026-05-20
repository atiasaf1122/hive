"""Worker trust scores — Section 5.5.

A trust score is `successful / (successful + failed)` per worker model
(`builder-sonnet`, `tester-llama-13b`, etc.). It's surfaced in
Settings → Trust profiles so the user can spot models that are
struggling, and used as a soft hint when the user picks a worker that
the recent data says they should think twice about.

Backed by the `worker_trust_scores` table introduced in
`backend/persistence/db.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.persistence.db import DB_PATH, get_conn


@dataclass
class TrustScore:
    worker_id: str
    successful_completions: int
    failed_validations: int
    total_sessions: int
    score: float          # 0-1
    last_updated: str

    @property
    def percentage(self) -> int:
        return round(self.score * 100)


def _compute_score(successful: int, failed: int) -> float:
    total = successful + failed
    if total == 0:
        return 1.0  # innocent until proven guilty
    return successful / total


async def record_completion(
    worker_id: str,
    *,
    passed_validation: bool,
    db_path: Path = DB_PATH,
) -> TrustScore:
    """Bump the counters for one worker after a completion + return the latest snapshot."""
    if not worker_id:
        raise ValueError("worker_id is required")

    async with get_conn(db_path) as conn:
        # Upsert pattern using INSERT ... ON CONFLICT.
        await conn.execute(
            """
            INSERT INTO worker_trust_scores
                (worker_id, successful_completions, failed_validations, total_sessions, last_updated)
            VALUES (?, ?, ?, 1, datetime('now'))
            ON CONFLICT(worker_id) DO UPDATE SET
                successful_completions = successful_completions + excluded.successful_completions,
                failed_validations     = failed_validations + excluded.failed_validations,
                total_sessions         = total_sessions + 1,
                last_updated           = datetime('now')
            """,
            (
                worker_id,
                1 if passed_validation else 0,
                0 if passed_validation else 1,
            ),
        )
        await conn.commit()

        cursor = await conn.execute(
            "SELECT * FROM worker_trust_scores WHERE worker_id = ?",
            (worker_id,),
        )
        row = await cursor.fetchone()

    return TrustScore(
        worker_id=row["worker_id"],
        successful_completions=int(row["successful_completions"]),
        failed_validations=int(row["failed_validations"]),
        total_sessions=int(row["total_sessions"]),
        score=_compute_score(
            int(row["successful_completions"]), int(row["failed_validations"]),
        ),
        last_updated=row["last_updated"],
    )


async def get_trust_score(
    worker_id: str,
    db_path: Path = DB_PATH,
) -> TrustScore | None:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM worker_trust_scores WHERE worker_id = ?",
            (worker_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return TrustScore(
        worker_id=row["worker_id"],
        successful_completions=int(row["successful_completions"]),
        failed_validations=int(row["failed_validations"]),
        total_sessions=int(row["total_sessions"]),
        score=_compute_score(
            int(row["successful_completions"]), int(row["failed_validations"]),
        ),
        last_updated=row["last_updated"],
    )


async def list_trust_scores(db_path: Path = DB_PATH) -> list[TrustScore]:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM worker_trust_scores ORDER BY total_sessions DESC",
        )
        rows = await cursor.fetchall()
    return [
        TrustScore(
            worker_id=r["worker_id"],
            successful_completions=int(r["successful_completions"]),
            failed_validations=int(r["failed_validations"]),
            total_sessions=int(r["total_sessions"]),
            score=_compute_score(
                int(r["successful_completions"]), int(r["failed_validations"]),
            ),
            last_updated=r["last_updated"],
        )
        for r in rows
    ]


async def reset_trust_score(
    worker_id: str,
    db_path: Path = DB_PATH,
) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute(
            "DELETE FROM worker_trust_scores WHERE worker_id = ?",
            (worker_id,),
        )
        await conn.commit()


# Below-this-floor is the "show a warning before using me" line.
LOW_TRUST_FLOOR = 0.70


def is_low_trust(score: TrustScore | None) -> bool:
    """Heuristic for the picker UI: warn the user before they choose a worker
    with poor recent performance — but only once we have enough data to be
    confident (≥ 10 sessions). New workers get a free pass."""
    if score is None:
        return False
    if score.total_sessions < 10:
        return False
    return score.score < LOW_TRUST_FLOOR
