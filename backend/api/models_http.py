"""Local model pool endpoint (E1) — what Ollama has, what it's good for,
and whether it fits VRAM right now."""
from __future__ import annotations

from fastapi import APIRouter

from backend.models_local import discover_local_models
from backend.resources import vram_manager

router = APIRouter(prefix="/api/models")


@router.get("/local/nudge")
async def local_models_nudge() -> dict:
    """Models discovered but never auditioned — 'new local model detected,
    audition it?'. Informational only; auditions never auto-run."""
    from backend.models_local import unauditioned_models
    pending = await unauditioned_models()
    return {"nudge": bool(pending), "models": pending}


@router.post("/local/audition/{model_name:path}")
async def local_model_audition(model_name: str) -> dict:
    """Run the fixed audition micro-tasks against one local model and store
    measured capabilities. Blocking (a few minutes of local generation +
    one tiny Haiku grade) — the desktop calls this from a button."""
    from backend.models_local import audition_model
    measured = await audition_model(model_name)
    return {"ok": True, "model": model_name, "measured": measured}


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
