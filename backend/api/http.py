"""REST API endpoints — orchestrator-first, multi-turn sessions."""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
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
from backend.orchestrator.graph import (
    SessionInterrupt,
    get_conversation_history,
    resume_session_with_value,
    run_session,
)
from backend.persistence.events import (
    create_session as db_create_session,
    get_session,
    list_agents,
    list_sessions,
    update_session_status,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# In-memory per-session state (lost on restart — sessions persist in SQLite)
_pending_approvals: dict[str, asyncio.Future] = {}
_pending_inputs: dict[str, asyncio.Future] = {}
_running_tasks: dict[str, asyncio.Task] = {}
_message_queues: dict[str, deque[str]] = {}


def _get_queue(session_id: str) -> deque[str]:
    q = _message_queues.get(session_id)
    if q is None:
        q = deque()
        _message_queues[session_id] = q
    return q


async def _session_runner(
    session_id: str,
    task: str,
    model: str,
    approval_mode: str,
    project_path: str,
    max_turns: int,
) -> None:
    """Drive a long-lived session: orchestrator turns, approvals, agent runs,
    and parking for the next user message — until the user closes the project."""
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
            payload = result.payload
            payload_type = payload.get("type")
            resume_value: dict | None = None

            if payload_type == "team_approval":
                await event_bus.emit(session_id, {
                    "type": "interrupt",
                    "session_id": session_id,
                    "payload": payload,
                })
                try:
                    from backend.telegram.notifier import notify_approval
                    await notify_approval(session_id, payload)
                except Exception as exc:
                    logger.debug("Telegram approval notify skipped: %s", exc)
                loop = asyncio.get_event_loop()
                future: asyncio.Future = loop.create_future()
                _pending_approvals[session_id] = future
                resume_value = await future
                _pending_approvals.pop(session_id, None)

            elif payload_type == "awaiting_input":
                # If a message was queued while agents were running, use it now.
                queue = _get_queue(session_id)
                if queue:
                    resume_value = {"text": queue.popleft()}
                else:
                    loop = asyncio.get_event_loop()
                    future = loop.create_future()
                    _pending_inputs[session_id] = future
                    resume_value = await future
                    _pending_inputs.pop(session_id, None)

            else:
                logger.warning("Unknown interrupt payload type %r — ending session", payload_type)
                break

            result = await resume_session_with_value(session_id, resume_value)

        # Graph reached END — happens when the user closes the project.
        await event_bus.emit(session_id, {
            "type": "session_end",
            "session_id": session_id,
            "status": "closed",
        })

    except Exception as exc:
        logger.exception("Session %s runner failed", session_id)
        await event_bus.emit(session_id, {
            "type": "session_error",
            "session_id": session_id,
            "error": str(exc),
        })
        try:
            await update_session_status(session_id, "failed")
        except Exception as upd_exc:
            logger.warning("Could not mark session %s as failed: %s", session_id, upd_exc)
    finally:
        _running_tasks.pop(session_id, None)
        _message_queues.pop(session_id, None)
        _pending_approvals.pop(session_id, None)
        _pending_inputs.pop(session_id, None)


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


# ── REST endpoints ───────────────────────────────────────────────────────────

def _resolve_workspace_path(raw: str | None, session_id: str) -> str:
    """Validate user-supplied workspace path, fall back to a session-local dir.

    The orchestrator's worktree pipeline assumes the path exists and is a
    directory. Without these checks, an empty / non-existent / file path
    surfaces as an opaque FileNotFoundError from inside uvloop when we
    later `cd` into it to run `git init`. We catch that here and return a
    clear 400 instead.
    """
    if raw is None or not raw.strip():
        # Treat empty as "use the session-local default" — backwards-
        # compatible with the old behaviour for callers that pass "".
        # (Pure missing field also lands here via the None branch.)
        if raw is None:
            workspace = Path.home() / ".hive" / "sessions" / session_id
            workspace.mkdir(parents=True, exist_ok=True)
            return str(workspace)
        # An explicitly-empty string is almost always a UI bug (the chip
        # was never populated) — refuse it loudly rather than silently
        # falling back, because the user thinks they chose a folder.
        raise HTTPException(
            status_code=400,
            detail="project_path is empty — choose a workspace folder.",
        )

    expanded = Path(raw).expanduser()
    if not expanded.exists():
        raise HTTPException(
            status_code=400,
            detail=f"project_path does not exist: {expanded}",
        )
    if not expanded.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"project_path is not a directory: {expanded}",
        )
    return str(expanded)


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session_endpoint(req: CreateSessionRequest) -> CreateSessionResponse:
    session_id = uuid.uuid4().hex[:8]
    project_path = _resolve_workspace_path(req.project_path, session_id)

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


@router.get("/sessions/{session_id}/history")
async def get_session_history(session_id: str) -> dict:
    """Return the orchestrator conversation history for the session."""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    history = await get_conversation_history(session_id)
    return {"session_id": session_id, "history": history}


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
    """Send a message to the orchestrator.

    If the graph is parked waiting for input → resume immediately.
    Otherwise (agents running, awaiting approval) → queue for next park.
    """
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")

    await event_bus.emit(session_id, {
        "type": "user_message",
        "session_id": session_id,
        "agent_id": req.agent_id,
        "text": text,
        "urgency": req.urgency,
    })

    future = _pending_inputs.get(session_id)
    if future and not future.done():
        future.set_result({"text": text})
        _pending_inputs.pop(session_id, None)
        return {"ok": True, "queued": False}

    _get_queue(session_id).append(text)
    return {"ok": True, "queued": True}


@router.post("/sessions/{session_id}/close")
async def close_session(session_id: str) -> dict:
    """Close the project. The orchestrator parks → END, session marked closed."""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    future = _pending_inputs.get(session_id)
    if future and not future.done():
        future.set_result({"close": True})
        _pending_inputs.pop(session_id, None)
        return {"ok": True, "status": "closing"}

    # Session isn't parked at wait_for_user — mark closed and let the runner
    # exit on its own. Any in-flight agents will finish.
    await update_session_status(session_id, "closed")
    await event_bus.emit(session_id, {
        "type": "session_closed",
        "session_id": session_id,
    })
    return {"ok": True, "status": "closed"}
