"""CRUD operations for pipelines and pipeline_runs."""
from __future__ import annotations

import uuid
from pathlib import Path

from backend.models import DEFAULT_MODEL
from backend.persistence.db import DB_PATH, get_conn


async def create_pipeline(
    name: str,
    task: str,
    model: str = DEFAULT_MODEL,
    approval_mode: str = "full-auto",
    schedule: str | None = None,
    db_path: Path = DB_PATH,
) -> str:
    pipeline_id = uuid.uuid4().hex[:12]
    webhook_token = uuid.uuid4().hex
    async with get_conn(db_path) as conn:
        await conn.execute(
            "INSERT INTO pipelines (id, name, task, model, approval_mode, schedule, webhook_token) "
            "VALUES (?,?,?,?,?,?,?)",
            (pipeline_id, name, task, model, approval_mode, schedule, webhook_token),
        )
        await conn.commit()
    return pipeline_id


async def get_pipeline(pipeline_id: str, db_path: Path = DB_PATH) -> dict | None:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,))
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_pipeline_by_webhook(token: str, db_path: Path = DB_PATH) -> dict | None:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM pipelines WHERE webhook_token=? AND enabled=1", (token,)
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def list_pipelines(db_path: Path = DB_PATH) -> list[dict]:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute("SELECT * FROM pipelines ORDER BY created_at DESC")
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def update_pipeline(
    pipeline_id: str,
    *,
    name: str | None = None,
    task: str | None = None,
    model: str | None = None,
    approval_mode: str | None = None,
    schedule: str | None = None,
    enabled: bool | None = None,
    db_path: Path = DB_PATH,
) -> None:
    fields: list[str] = []
    values: list = []
    if name is not None:
        fields.append("name=?"); values.append(name)
    if task is not None:
        fields.append("task=?"); values.append(task)
    if model is not None:
        fields.append("model=?"); values.append(model)
    if approval_mode is not None:
        fields.append("approval_mode=?"); values.append(approval_mode)
    if schedule is not None:
        fields.append("schedule=?"); values.append(schedule)
    if enabled is not None:
        fields.append("enabled=?"); values.append(1 if enabled else 0)
    if not fields:
        return
    values.append(pipeline_id)
    async with get_conn(db_path) as conn:
        await conn.execute(f"UPDATE pipelines SET {', '.join(fields)} WHERE id=?", values)
        await conn.commit()


async def delete_pipeline(pipeline_id: str, db_path: Path = DB_PATH) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute("DELETE FROM pipelines WHERE id=?", (pipeline_id,))
        await conn.commit()


async def record_pipeline_run(
    pipeline_id: str,
    session_id: str,
    triggered_by: str = "manual",
    db_path: Path = DB_PATH,
) -> str:
    run_id = uuid.uuid4().hex[:12]
    async with get_conn(db_path) as conn:
        await conn.execute(
            "INSERT INTO pipeline_runs (id, pipeline_id, session_id, triggered_by) VALUES (?,?,?,?)",
            (run_id, pipeline_id, session_id, triggered_by),
        )
        await conn.execute(
            "UPDATE pipelines SET last_run_at=datetime('now') WHERE id=?",
            (pipeline_id,),
        )
        await conn.commit()
    return run_id


async def finish_pipeline_run(
    run_id: str, status: str, db_path: Path = DB_PATH
) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute(
            "UPDATE pipeline_runs SET status=?, ended_at=datetime('now') WHERE id=?",
            (status, run_id),
        )
        await conn.commit()


async def list_pipeline_runs(
    pipeline_id: str, limit: int = 20, db_path: Path = DB_PATH
) -> list[dict]:
    async with get_conn(db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM pipeline_runs WHERE pipeline_id=? ORDER BY started_at DESC LIMIT ?",
            (pipeline_id, limit),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]
