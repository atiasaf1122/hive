"""HTTP surface for the validation stack.

    GET    /api/validation/trust              — all worker trust scores
    GET    /api/validation/trust/{worker_id}  — one
    DELETE /api/validation/trust/{worker_id}  — reset
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.validation.trust import (
    LOW_TRUST_FLOOR,
    get_trust_score,
    list_trust_scores,
    reset_trust_score,
)

router = APIRouter(prefix="/api/validation")


def _to_dict(s) -> dict:
    return {
        "worker_id": s.worker_id,
        "successful_completions": s.successful_completions,
        "failed_validations": s.failed_validations,
        "total_sessions": s.total_sessions,
        "score": s.score,
        "percentage": s.percentage,
        "last_updated": s.last_updated,
        "low_trust": s.total_sessions >= 10 and s.score < LOW_TRUST_FLOOR,
    }


@router.get("/trust")
async def list_trust() -> dict:
    items = await list_trust_scores()
    return {
        "items": [_to_dict(s) for s in items],
        "low_trust_floor": LOW_TRUST_FLOOR,
    }


@router.get("/trust/{worker_id}")
async def one_trust(worker_id: str) -> dict:
    s = await get_trust_score(worker_id)
    if s is None:
        raise HTTPException(404, "no trust data yet for that worker")
    return _to_dict(s)


@router.delete("/trust/{worker_id}")
async def reset_trust(worker_id: str) -> dict:
    await reset_trust_score(worker_id)
    return {"ok": True, "worker_id": worker_id}
