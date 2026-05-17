"""REST API endpoints."""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

from backend.api import event_bus
from backend.api.schemas import (
    AgentInfo,
    ApproveRequest,
    CreateSessionRequest,
    CreateSessionResponse,
    MessageRequest,
    SessionInfo,
)
from backend.orchestrator.graph import SessionInterrupt, resume_session_with_value, run_session
from backend.persistence.events import create_session as db_create_session
from backend.persistence.events import get_session, list_agents, list_sessions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# In-memory state (lost on restart — sessions persist in SQLite)
_pending_approvals: dict[str, asyncio.Future] = {}
_running_tasks: dict[str, asyncio.Task] = {}


async def _session_runner(
    session_id: str,
    task: str,
    model: str,
    approval_mode: str,
    project_path: str,
    max_turns: int,
) -> None:
    """Background coroutine that drives a session to completion, handling interrupts."""
    agent_id = f"worker-{session_id[:8]}"
    await event_bus.emit(session_id, {"type": "session_start", "session_id": session_id, "task": task})

    try:
        result = await run_session(
            session_id=session_id,
            agent_id=agent_id,
            task=task,
            model=model,
            worktree_path=project_path,
            max_turns=max_turns,
            approval_mode=approval_mode,
        )

        while isinstance(result, SessionInterrupt):
            await event_bus.emit(session_id, {
                "type": "interrupt",
                "session_id": session_id,
                "payload": result.payload,
            })
            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()
            _pending_approvals[session_id] = future
            resume_value = await future
            _pending_approvals.pop(session_id, None)
            result = await resume_session_with_value(session_id, resume_value)

        status = "completed"
        text_output = ""
        cost_usd = 0.0
        if result:
            r = result if isinstance(result, dict) else dict(result)
            status = r.get("status", "completed")
            text_output = r.get("text_output", "")
            cost_usd = r.get("cost_usd", 0.0)

        await event_bus.emit(session_id, {
            "type": "session_end",
            "session_id": session_id,
            "status": status,
            "text_output": text_output[:500] if text_output else "",
            "cost_usd": cost_usd,
        })

    except Exception as exc:
        logger.exception("Session %s runner failed", session_id)
        await event_bus.emit(session_id, {
            "type": "session_error",
            "session_id": session_id,
            "error": str(exc),
        })
    finally:
        _running_tasks.pop(session_id, None)


def launch_session(
    session_id: str,
    task: str,
    model: str,
    approval_mode: str,
    project_path: str,
    max_turns: int = 20,
) -> asyncio.Task:
    """Create and register a background session task. Used by HTTP endpoints and the scheduler."""
    t = asyncio.create_task(
        _session_runner(
            session_id=session_id,
            task=task,
            model=model,
            approval_mode=approval_mode,
            project_path=project_path,
            max_turns=max_turns,
        ),
        name=f"session-{session_id}",
    )
    _running_tasks[session_id] = t
    return t


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session_endpoint(req: CreateSessionRequest) -> CreateSessionResponse:
    session_id = uuid.uuid4().hex[:8]
    if req.project_path:
        project_path = req.project_path
    else:
        workspace = Path.home() / ".hive" / "sessions" / session_id
        workspace.mkdir(parents=True, exist_ok=True)
        project_path = str(workspace)

    # Pre-create the DB record so approval_mode is persisted immediately
    await db_create_session(session_id, name=req.task[:80], approval_mode=req.approval_mode)

    launch_session(
        session_id=session_id,
        task=req.task,
        model=req.model,
        approval_mode=req.approval_mode,
        project_path=project_path,
        max_turns=req.max_turns,
    )
    return CreateSessionResponse(session_id=session_id)


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions_endpoint() -> list[SessionInfo]:
    sessions = await list_sessions()
    return [
        SessionInfo(
            session_id=s["id"],
            name=s["name"],
            status=s["status"],
            approval_mode=s.get("approval_mode", "full-auto"),
            created_at=s.get("created_at", ""),
            last_active=s.get("last_active", ""),
        )
        for s in sessions
    ]


@router.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session_endpoint(session_id: str) -> SessionInfo:
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    agents = await list_agents(session_id)
    return SessionInfo(
        session_id=session["id"],
        name=session["name"],
        status=session["status"],
        approval_mode=session.get("approval_mode", "full-auto"),
        created_at=session.get("created_at", ""),
        last_active=session.get("last_active", ""),
        agents=[
            AgentInfo(
                agent_id=a["id"],
                role=a["role"],
                model=a["model"],
                status=a["status"],
            )
            for a in agents
        ],
    )


@router.post("/sessions/{session_id}/approve")
async def approve_session(session_id: str, req: ApproveRequest) -> dict:
    future = _pending_approvals.get(session_id)
    if not future:
        raise HTTPException(status_code=404, detail="No pending approval for this session")
    resume_value: dict = {"approved": req.approved}
    if req.team_composition:
        resume_value["team_composition"] = req.team_composition
    future.set_result(resume_value)
    return {"ok": True}


@router.post("/sessions/{session_id}/message")
async def send_message(session_id: str, req: MessageRequest) -> dict:
    await event_bus.emit(session_id, {
        "type": "user_message",
        "session_id": session_id,
        "agent_id": req.agent_id,
        "text": req.text,
        "urgency": req.urgency,
    })
    return {"ok": True}
