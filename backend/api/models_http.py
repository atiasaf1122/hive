"""Local model pool endpoint (E1) — what Ollama has, what it's good for,
and whether it fits VRAM right now."""
from __future__ import annotations

from fastapi import APIRouter

from backend.models_local import discover_local_models
from backend.resources import vram_manager

router = APIRouter(prefix="/api/models")


@router.get("/local")
async def local_models() -> dict:
    models = await discover_local_models()
    snap = await vram_manager.snapshot()
    return {
        "ollama": bool(models),
        "models": [m.as_dict() for m in models],
        "vram": None if snap is None else {
            "gpus": [{"index": g.index, "name": g.name,
                      "total_mb": g.total_mb, "used_mb": g.used_mb}
                     for g in snap.gpus],
            "reserved_mb": snap.reserved_mb,
            "headroom_mb": snap.headroom_mb,
        },
    }
