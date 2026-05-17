"""APScheduler integration — fires pipelines on cron schedules."""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.persistence.db import DB_PATH
from backend.pipelines.store import (
    finish_pipeline_run,
    list_pipelines,
    record_pipeline_run,
)

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _fire_pipeline(pipeline_id: str, db_path: Path = DB_PATH) -> None:
    """Triggered by APScheduler. Launches a session for the pipeline."""
    from backend.pipelines.store import get_pipeline
    from backend.api.http import launch_session
    from backend.persistence.events import create_session as db_create_session

    pipeline = await get_pipeline(pipeline_id, db_path)
    if not pipeline or not pipeline["enabled"]:
        return

    session_id = uuid.uuid4().hex[:8]
    workspace = Path.home() / ".hive" / "sessions" / session_id
    workspace.mkdir(parents=True, exist_ok=True)

    await db_create_session(
        session_id,
        name=pipeline["task"][:80],
        approval_mode=pipeline["approval_mode"],
        db_path=db_path,
    )
    run_id = await record_pipeline_run(pipeline_id, session_id, triggered_by="schedule", db_path=db_path)

    logger.info("Scheduler firing pipeline=%s session=%s", pipeline_id, session_id)

    task = launch_session(
        session_id=session_id,
        task=pipeline["task"],
        model=pipeline["model"],
        approval_mode=pipeline["approval_mode"],
        project_path=str(workspace),
        max_turns=20,
    )

    async def _watch(t: asyncio.Task, rid: str) -> None:
        try:
            await t
            await finish_pipeline_run(rid, "completed", db_path)
        except Exception:
            await finish_pipeline_run(rid, "failed", db_path)

    asyncio.create_task(_watch(task, run_id))


def _parse_cron(cron_expr: str) -> CronTrigger:
    """Parse a 5-field cron string into an APScheduler CronTrigger."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5 cron fields, got: {cron_expr!r}")
    minute, hour, day, month, day_of_week = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
    )


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


async def start_scheduler(db_path: Path = DB_PATH) -> None:
    """Load all enabled scheduled pipelines and start APScheduler."""
    scheduler = get_scheduler()
    pipelines = await list_pipelines(db_path)
    for p in pipelines:
        if p["enabled"] and p["schedule"]:
            _add_pipeline_job(scheduler, p["id"], p["schedule"])

    scheduler.start()
    logger.info("Scheduler started with %d pipeline jobs", len(scheduler.get_jobs()))


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def _add_pipeline_job(
    scheduler: AsyncIOScheduler, pipeline_id: str, schedule: str
) -> None:
    try:
        trigger = _parse_cron(schedule)
        job_id = f"pipeline-{pipeline_id}"
        scheduler.add_job(
            _fire_pipeline,
            trigger=trigger,
            id=job_id,
            args=[pipeline_id],
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("Scheduled pipeline=%s cron=%r", pipeline_id, schedule)
    except Exception as exc:
        logger.warning("Could not schedule pipeline=%s: %s", pipeline_id, exc)


def sync_pipeline_schedule(pipeline_id: str, schedule: str | None, enabled: bool) -> None:
    """Add, update, or remove a pipeline's scheduled job. Called after create/update/delete."""
    scheduler = get_scheduler()
    if not scheduler.running:
        return
    job_id = f"pipeline-{pipeline_id}"
    scheduler.remove_job(job_id, jobstore=None) if scheduler.get_job(job_id) else None
    if schedule and enabled:
        _add_pipeline_job(scheduler, pipeline_id, schedule)
