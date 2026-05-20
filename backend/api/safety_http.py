"""Safety HTTP surface.

    GET    /api/safety/limits/defaults              — global HARD_STOPS
    GET    /api/safety/breakers                     — per-worker breaker snapshot
    POST   /api/safety/breakers/{worker_id}/reset
    GET    /api/safety/sessions/{id}/override       — per-session override + effective limits
    PUT    /api/safety/sessions/{id}/override
    DELETE /api/safety/sessions/{id}/override       — clear, fall back to defaults
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.safety.circuit_breaker import default_registry
from backend.safety.hard_stops import DEFAULTS
from backend.safety.overrides import (
    SafetyOverride,
    clear_override,
    effective_limits,
    load_override,
    save_override,
)

router = APIRouter(prefix="/api/safety")


class HardStopsResponse(BaseModel):
    max_concurrent_agents: int
    max_session_duration_hours: float
    max_same_file_edits: int
    vram_threshold_percent: int
    disk_min_free_gb: float
    max_tokens_per_autonomous_run: int


@router.get("/limits/defaults", response_model=HardStopsResponse)
def hard_stop_defaults() -> HardStopsResponse:
    """The non-overridable ceiling values shipped with this build."""
    return HardStopsResponse(
        max_concurrent_agents=DEFAULTS.max_concurrent_agents,
        max_session_duration_hours=DEFAULTS.max_session_duration_hours,
        max_same_file_edits=DEFAULTS.max_same_file_edits,
        vram_threshold_percent=DEFAULTS.vram_threshold_percent,
        disk_min_free_gb=DEFAULTS.disk_min_free_gb,
        max_tokens_per_autonomous_run=DEFAULTS.max_tokens_per_autonomous_run,
    )


@router.get("/breakers")
def breakers() -> dict:
    return {"items": default_registry.snapshot()}


@router.post("/breakers/{worker_id}/reset")
def reset_breaker(worker_id: str) -> dict:
    default_registry.reset(worker_id)
    return {"ok": True, "worker_id": worker_id}


# ── Per-session overrides ──────────────────────────────────────────────────

class OverrideBody(BaseModel):
    max_tokens_per_autonomous_run: int | None = None
    max_session_duration_hours: float | None = None
    max_concurrent_agents: int | None = None
    max_same_file_edits: int | None = None
    notify_at_burn_ratio: float | None = None


@router.get("/sessions/{session_id}/override")
async def get_session_override(session_id: str) -> dict:
    """Return the saved override (or empty) plus the resolved effective limits."""
    override = await load_override(session_id)
    effective = await effective_limits(session_id)
    return {
        "session_id": session_id,
        "override": override.to_dict(),
        "effective": {
            "max_concurrent_agents": effective.max_concurrent_agents,
            "max_session_duration_hours": effective.max_session_duration_hours,
            "max_same_file_edits": effective.max_same_file_edits,
            "vram_threshold_percent": effective.vram_threshold_percent,
            "disk_min_free_gb": effective.disk_min_free_gb,
            "max_tokens_per_autonomous_run": effective.max_tokens_per_autonomous_run,
        },
        "defaults": {
            "max_concurrent_agents": DEFAULTS.max_concurrent_agents,
            "max_session_duration_hours": DEFAULTS.max_session_duration_hours,
            "max_same_file_edits": DEFAULTS.max_same_file_edits,
            "max_tokens_per_autonomous_run": DEFAULTS.max_tokens_per_autonomous_run,
        },
    }


@router.put("/sessions/{session_id}/override")
async def put_session_override(session_id: str, body: OverrideBody) -> dict:
    """Replace the saved override. None fields fall back to defaults."""
    override = SafetyOverride(
        max_tokens_per_autonomous_run=body.max_tokens_per_autonomous_run,
        max_session_duration_hours=body.max_session_duration_hours,
        max_concurrent_agents=body.max_concurrent_agents,
        max_same_file_edits=body.max_same_file_edits,
        notify_at_burn_ratio=body.notify_at_burn_ratio,
    )
    await save_override(session_id, override)
    return {"ok": True, "session_id": session_id, "override": override.to_dict()}


@router.delete("/sessions/{session_id}/override")
async def delete_session_override(session_id: str) -> dict:
    await clear_override(session_id)
    return {"ok": True, "session_id": session_id}
