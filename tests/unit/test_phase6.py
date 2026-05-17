"""Phase 6 tests — pipelines CRUD, scheduler, webhook trigger."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from backend.persistence.db import init_db
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
from backend.pipelines.scheduler import _parse_cron


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    await init_db(db_path)
    return db_path


# ── store tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_pipeline(db: Path) -> None:
    pid = await create_pipeline("Daily haiku", "Write a haiku about Python", db_path=db)
    assert len(pid) == 12
    p = await get_pipeline(pid, db_path=db)
    assert p is not None
    assert p["name"] == "Daily haiku"
    assert p["task"] == "Write a haiku about Python"
    assert p["enabled"] == 1
    assert p["schedule"] is None
    assert len(p["webhook_token"]) == 32


@pytest.mark.asyncio
async def test_create_pipeline_with_schedule(db: Path) -> None:
    pid = await create_pipeline("Hourly", "Do something", schedule="0 * * * *", db_path=db)
    p = await get_pipeline(pid, db_path=db)
    assert p["schedule"] == "0 * * * *"


@pytest.mark.asyncio
async def test_list_pipelines(db: Path) -> None:
    await create_pipeline("A", "task A", db_path=db)
    await create_pipeline("B", "task B", db_path=db)
    pipelines = await list_pipelines(db_path=db)
    assert len(pipelines) == 2


@pytest.mark.asyncio
async def test_update_pipeline(db: Path) -> None:
    pid = await create_pipeline("Old name", "old task", db_path=db)
    await update_pipeline(pid, name="New name", schedule="30 9 * * 1", db_path=db)
    p = await get_pipeline(pid, db_path=db)
    assert p["name"] == "New name"
    assert p["schedule"] == "30 9 * * 1"


@pytest.mark.asyncio
async def test_update_pipeline_disable(db: Path) -> None:
    pid = await create_pipeline("Toggle", "task", db_path=db)
    await update_pipeline(pid, enabled=False, db_path=db)
    p = await get_pipeline(pid, db_path=db)
    assert p["enabled"] == 0


@pytest.mark.asyncio
async def test_delete_pipeline(db: Path) -> None:
    pid = await create_pipeline("Temp", "task", db_path=db)
    await delete_pipeline(pid, db_path=db)
    assert await get_pipeline(pid, db_path=db) is None


@pytest.mark.asyncio
async def test_get_pipeline_by_webhook(db: Path) -> None:
    pid = await create_pipeline("Webhook test", "task", db_path=db)
    p = await get_pipeline(pid, db_path=db)
    token = p["webhook_token"]
    found = await get_pipeline_by_webhook(token, db_path=db)
    assert found is not None
    assert found["id"] == pid


@pytest.mark.asyncio
async def test_webhook_disabled_pipeline_not_found(db: Path) -> None:
    pid = await create_pipeline("Disabled", "task", db_path=db)
    await update_pipeline(pid, enabled=False, db_path=db)
    p = await get_pipeline(pid, db_path=db)
    found = await get_pipeline_by_webhook(p["webhook_token"], db_path=db)
    assert found is None


@pytest.mark.asyncio
async def test_record_and_finish_run(db: Path) -> None:
    pid = await create_pipeline("Runner", "task", db_path=db)
    run_id = await record_pipeline_run(pid, "ses123", triggered_by="manual", db_path=db)
    assert len(run_id) == 12
    runs = await list_pipeline_runs(pid, db_path=db)
    assert len(runs) == 1
    assert runs[0]["status"] == "running"
    assert runs[0]["session_id"] == "ses123"

    await finish_pipeline_run(run_id, "completed", db_path=db)
    runs = await list_pipeline_runs(pid, db_path=db)
    assert runs[0]["status"] == "completed"
    assert runs[0]["ended_at"] is not None


@pytest.mark.asyncio
async def test_multiple_runs(db: Path) -> None:
    pid = await create_pipeline("Multi", "task", db_path=db)
    for i in range(3):
        run_id = await record_pipeline_run(pid, f"ses{i}", db_path=db)
        await finish_pipeline_run(run_id, "completed", db_path=db)
    runs = await list_pipeline_runs(pid, db_path=db)
    assert len(runs) == 3


# ── scheduler cron parsing ────────────────────────────────────────────────────

def test_parse_cron_daily() -> None:
    trigger = _parse_cron("0 17 * * *")
    assert trigger is not None


def test_parse_cron_weekly_monday() -> None:
    trigger = _parse_cron("30 9 * * 1")
    assert trigger is not None


def test_parse_cron_every_hour() -> None:
    trigger = _parse_cron("0 * * * *")
    assert trigger is not None


def test_parse_cron_invalid() -> None:
    with pytest.raises(ValueError):
        _parse_cron("bad cron")


def test_parse_cron_wrong_fields() -> None:
    with pytest.raises(ValueError):
        _parse_cron("* * * *")  # only 4 fields
