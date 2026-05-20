"""REST endpoints for pipeline CRUD and manual triggering."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.persistence.db import DB_PATH
from backend.persistence.events import create_session as db_create_session
from backend.pipelines.scheduler import sync_pipeline_schedule
from backend.pipelines.store import (
    create_pipeline,
    delete_pipeline,
    finish_pipeline_run,
    get_pipeline,
    get_pipeline_by_webhook,
    list_pipeline_runs,
    list_pipelines,
    record_pipeline_run,
    update_pipeline,
)

router = APIRouter(prefix="/api/pipelines")


class CreatePipelineRequest(BaseModel):
    name: str
    task: str
    model: str = "claude:sonnet"
    approval_mode: str = "full-auto"
    schedule: str | None = None


class UpdatePipelineRequest(BaseModel):
    name: str | None = None
    task: str | None = None
    model: str | None = None
    approval_mode: str | None = None
    schedule: str | None = None
    enabled: bool | None = None


class PipelineOut(BaseModel):
    id: str
    name: str
    task: str
    model: str
    approval_mode: str
    schedule: str | None
    webhook_token: str
    enabled: bool
    created_at: str
    last_run_at: str | None
    next_run_at: str | None


class PipelineRunOut(BaseModel):
    id: str
    pipeline_id: str
    session_id: str | None
    triggered_by: str
    status: str
    started_at: str
    ended_at: str | None


def _pipeline_to_out(p: dict) -> PipelineOut:
    return PipelineOut(
        id=p["id"],
        name=p["name"],
        task=p["task"],
        model=p["model"],
        approval_mode=p["approval_mode"],
        schedule=p.get("schedule"),
        webhook_token=p.get("webhook_token", ""),
        enabled=bool(p["enabled"]),
        created_at=p.get("created_at", ""),
        last_run_at=p.get("last_run_at"),
        next_run_at=p.get("next_run_at"),
    )


@router.get("", response_model=list[PipelineOut])
async def list_pipelines_endpoint() -> list[PipelineOut]:
    pipelines = await list_pipelines()
    return [_pipeline_to_out(p) for p in pipelines]


@router.post("", response_model=PipelineOut)
async def create_pipeline_endpoint(req: CreatePipelineRequest) -> PipelineOut:
    pipeline_id = await create_pipeline(
        name=req.name,
        task=req.task,
        model=req.model,
        approval_mode=req.approval_mode,
        schedule=req.schedule,
    )
    if req.schedule:
        sync_pipeline_schedule(pipeline_id, req.schedule, enabled=True)
    p = await get_pipeline(pipeline_id)
    return _pipeline_to_out(p)  # type: ignore[arg-type]


@router.get("/{pipeline_id}", response_model=PipelineOut)
async def get_pipeline_endpoint(pipeline_id: str) -> PipelineOut:
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return _pipeline_to_out(p)


@router.patch("/{pipeline_id}", response_model=PipelineOut)
async def update_pipeline_endpoint(pipeline_id: str, req: UpdatePipelineRequest) -> PipelineOut:
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    await update_pipeline(
        pipeline_id,
        name=req.name,
        task=req.task,
        model=req.model,
        approval_mode=req.approval_mode,
        schedule=req.schedule,
        enabled=req.enabled,
    )
    updated = await get_pipeline(pipeline_id)
    sync_pipeline_schedule(
        pipeline_id,
        updated["schedule"],  # type: ignore[index]
        enabled=bool(updated["enabled"]),  # type: ignore[index]
    )
    return _pipeline_to_out(updated)  # type: ignore[arg-type]


@router.delete("/{pipeline_id}", status_code=204)
async def delete_pipeline_endpoint(pipeline_id: str) -> None:
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    sync_pipeline_schedule(pipeline_id, None, enabled=False)
    await delete_pipeline(pipeline_id)


async def _trigger_pipeline_run(p: dict, run_id: str, session_id: str, workspace: Path) -> None:
    """Common helper for /run and /webhook — launches and watches the session task."""
    import asyncio
    import logging

    from backend.api.http import launch_session

    logger = logging.getLogger(__name__)

    task = launch_session(
        session_id=session_id,
        task=p["task"],
        model=p["model"],
        approval_mode=p["approval_mode"],
        project_path=str(workspace),
    )

    async def _watch(t: asyncio.Task, rid: str) -> None:
        try:
            await t
            await finish_pipeline_run(rid, "completed")
        except Exception as exc:
            logger.warning("Pipeline run %s failed: %s", rid, exc)
            await finish_pipeline_run(rid, "failed")

    asyncio.create_task(_watch(task, run_id))


@router.post("/{pipeline_id}/run", response_model=dict)
async def run_pipeline_now(pipeline_id: str) -> dict:
    """Manually trigger a pipeline run immediately."""
    p = await get_pipeline(pipeline_id)
    if not p:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    session_id = uuid.uuid4().hex[:8]
    workspace = Path.home() / ".hive" / "sessions" / session_id
    workspace.mkdir(parents=True, exist_ok=True)

    await db_create_session(session_id, name=p["task"][:80], approval_mode=p["approval_mode"])
    run_id = await record_pipeline_run(pipeline_id, session_id, triggered_by="manual")

    await _trigger_pipeline_run(p, run_id, session_id, workspace)
    return {"session_id": session_id, "run_id": run_id}


@router.post("/webhook/{token}", response_model=dict)
async def webhook_trigger(token: str) -> dict:
    """Trigger a pipeline via its webhook token."""
    p = await get_pipeline_by_webhook(token)
    if not p:
        raise HTTPException(status_code=404, detail="No pipeline for this token")

    session_id = uuid.uuid4().hex[:8]
    workspace = Path.home() / ".hive" / "sessions" / session_id
    workspace.mkdir(parents=True, exist_ok=True)

    await db_create_session(session_id, name=p["task"][:80], approval_mode=p["approval_mode"])
    run_id = await record_pipeline_run(p["id"], session_id, triggered_by="webhook")

    await _trigger_pipeline_run(p, run_id, session_id, workspace)
    return {"session_id": session_id, "run_id": run_id}


@router.get("/{pipeline_id}/runs", response_model=list[PipelineRunOut])
async def list_runs_endpoint(pipeline_id: str, limit: int = 20) -> list[PipelineRunOut]:
    runs = await list_pipeline_runs(pipeline_id, limit=limit)
    return [
        PipelineRunOut(
            id=r["id"],
            pipeline_id=r["pipeline_id"],
            session_id=r.get("session_id"),
            triggered_by=r["triggered_by"],
            status=r["status"],
            started_at=r["started_at"],
            ended_at=r.get("ended_at"),
        )
        for r in runs
    ]
