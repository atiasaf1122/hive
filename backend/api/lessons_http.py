"""Lessons HTTP surface (D1.5 UI backing).

    GET    /api/lessons?status=active|archived     — list with stats
    POST   /api/lessons/{id}/archive               — manual archive
    POST   /api/lessons/{id}/restore               — manual restore
    DELETE /api/lessons/{id}                       — hard delete
    POST   /api/lessons/distill/{session_id}       — "distill now" button
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from backend.lessons.store import (
    delete_lesson,
    get_lesson,
    list_lessons,
    set_lesson_status,
)

router = APIRouter(prefix="/api/lessons")


@router.get("")
async def get_lessons(status: str | None = None) -> dict:
    lessons = await list_lessons(status=status)
    return {"lessons": [asdict(lesson) for lesson in lessons]}


@router.post("/{lesson_id}/archive")
async def archive_lesson(lesson_id: int) -> dict:
    if await get_lesson(lesson_id) is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    await set_lesson_status(lesson_id, "archived")
    return {"ok": True}


@router.post("/{lesson_id}/restore")
async def restore_lesson(lesson_id: int) -> dict:
    if await get_lesson(lesson_id) is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    await set_lesson_status(lesson_id, "active")
    return {"ok": True}


@router.delete("/{lesson_id}")
async def remove_lesson(lesson_id: int) -> dict:
    if await get_lesson(lesson_id) is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    await delete_lesson(lesson_id)
    return {"ok": True}


@router.post("/distill/{session_id}")
async def distill_now(session_id: str) -> dict:
    """Manual 'distill now from session X' — same grounded pipeline."""
    from backend.lessons.service import distill_session_lessons
    from backend.persistence.events import get_session

    session = await get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    saved = await distill_session_lessons(session_id, session.get("path") or None)
    return {"ok": True, "saved_lesson_ids": saved}
