"""REST API endpoints — orchestrator-first, multi-turn sessions."""
from __future__ import annotations

import asyncio
import logging
import os
import re
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
    resume_session,
    resume_session_with_value,
    run_session,
)
from backend.persistence.events import (
    create_pending_approval,
    create_session as db_create_session,
    get_pending_approval,
    get_session,
    list_agents,
    list_pending_approvals,
    list_sessions,
    resolve_pending_approval,
    update_session_status,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# In-memory per-session state (lost on restart — sessions persist in SQLite)
# `_pending_approvals` is keyed by correlation_id (NOT session_id) — a single
# session can have multiple approvals in flight (invariant #5). The mirror
# `_session_to_corr_ids` indexes session → set of corr_ids so cancel/close
# can release every waiter for that session in one pass.
_pending_approvals: dict[str, asyncio.Future] = {}
_session_to_corr_ids: dict[str, set[str]] = {}
_pending_inputs: dict[str, asyncio.Future] = {}
_running_tasks: dict[str, asyncio.Task] = {}
_message_queues: dict[str, deque[str]] = {}
# Session ids for which /close was requested while the runner was not
# parked. The runner checks this on every loop iteration so the close
# happens cleanly at the next safe point instead of leaving the runner
# emitting events on a session the UI already considers closed.
_close_requested_sessions: set[str] = set()


def _register_approval(correlation_id: str, session_id: str, future: asyncio.Future) -> None:
    _pending_approvals[correlation_id] = future
    _session_to_corr_ids.setdefault(session_id, set()).add(correlation_id)


def _unregister_approval(correlation_id: str, session_id: str) -> None:
    _pending_approvals.pop(correlation_id, None)
    bucket = _session_to_corr_ids.get(session_id)
    if bucket is not None:
        bucket.discard(correlation_id)
        if not bucket:
            _session_to_corr_ids.pop(session_id, None)


def _session_corr_ids(session_id: str) -> list[str]:
    return list(_session_to_corr_ids.get(session_id, ()))


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
    resume: bool = False,
) -> None:
    """Drive a long-lived session: orchestrator turns, approvals, agent runs,
    and parking for the next user message — until the user closes the project.

    `resume=True` re-attaches a runner to an existing session (after a backend
    restart left it 'idle'): instead of starting a fresh graph run, we pick up
    from the LangGraph checkpoint — usually a parked awaiting_input interrupt —
    and enter the same interrupt loop.
    """
    agent_id = f"worker-{session_id[:8]}"
    await event_bus.emit(session_id, {
        "type": "session_resumed" if resume else "session_start",
        "session_id": session_id,
        "task": task,
    })

    # Watchdog: if the orchestrator's first claude subprocess doesn't
    # emit anything for ORCH_STALL_WARN_S, push a diagnostic event so
    # the UI / Telegram can show "still thinking…" rather than a silent
    # void. This was the failure mode of the snake-game stall bug —
    # nothing reached the WebSocket and the session looked frozen.
    stall_warn_s = float(os.environ.get("HIVE_ORCH_STALL_WARN_S", "30"))

    # Capture the baseline BEFORE creating the watchdog task so the
    # check doesn't race with the orchestrator's first emit. Without
    # this, the orchestrator's "thinking" event lands before the
    # watchdog task starts running, baseline catches up to it, and any
    # subsequent silence (e.g. waiting on Haiku) trips the warning
    # spuriously.
    baseline_id = event_bus.latest_event_id(session_id)

    async def _orchestrator_heartbeat() -> None:
        # Only fire the stall warning if the orchestrator hasn't produced
        # ANY event past the initial session_start by the deadline.
        try:
            await asyncio.sleep(stall_warn_s)
            if event_bus.latest_event_id(session_id) > baseline_id:
                return  # already streaming — silence the warning
            await event_bus.emit(session_id, {
                "type": "orchestrator_stall_hint",
                "session_id": session_id,
                "elapsed_s": stall_warn_s,
                "hint": (
                    f"Orchestrator hasn't streamed anything in {stall_warn_s:.0f}s. "
                    "If this keeps happening, check `claude --version`, your "
                    "OAuth token, and the backend logs for a subprocess error."
                ),
            })
        except asyncio.CancelledError:
            return

    heartbeat = asyncio.create_task(_orchestrator_heartbeat())

    # `user_close_requested` is set ONLY by /close (or /cancel). The
    # _session_runner uses this to decide whether a normal loop exit
    # means "the user genuinely closed the project" or "the graph
    # reached END unexpectedly". Without this guard, every successful
    # turn that mistakenly fell through to END would silently close
    # the session — user saw "project closed itself" on a finished
    # snake/tetris build.
    close_requested = False
    try:
        if resume:
            result = await resume_session(session_id)
            if result is None:
                # No checkpoint — the session row exists but the graph never
                # ran (or the checkpoint DB was wiped). Nothing to resume.
                logger.warning("Session %s has no checkpoint to resume", session_id)
                await event_bus.emit(session_id, {
                    "type": "session_error",
                    "session_id": session_id,
                    "error": "Nothing to resume — this session has no saved state.",
                })
                await update_session_status(session_id, "idle")
                return
        else:
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
            # If /close was POSTed while we were mid-execution, honour it
            # here at the next safe parking point instead of letting the
            # runner keep emitting events on an already-closed session.
            if session_id in _close_requested_sessions:
                close_requested = True
                break
            payload = result.payload
            payload_type = payload.get("type")
            resume_value: dict | None = None

            if payload_type == "team_approval":
                correlation_id = uuid.uuid4().hex
                # Persist BEFORE awaiting — a backend restart at this point
                # must be able to replay the interrupt to reconnecting
                # clients (invariant #5). Without this row, the runner's
                # await is the only record this approval was ever requested.
                await create_pending_approval(
                    correlation_id=correlation_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    request_payload=payload,
                )
                # Stamp the correlation_id into the WS payload so the UI
                # carries it back on POST /approve. The dict is mutated
                # in place — payload is shaped by the orchestrator and
                # not referenced again here.
                payload = {**payload, "correlation_id": correlation_id}
                await event_bus.emit(session_id, {
                    "type": "interrupt",
                    "session_id": session_id,
                    "correlation_id": correlation_id,
                    "payload": payload,
                })
                try:
                    from backend.telegram.notifier import notify_approval
                    await notify_approval(session_id, payload, correlation_id=correlation_id)
                except Exception as exc:
                    logger.debug("Telegram approval notify skipped: %s", exc)
                loop = asyncio.get_event_loop()
                future: asyncio.Future = loop.create_future()
                _register_approval(correlation_id, session_id, future)
                try:
                    resume_value = await future
                finally:
                    _unregister_approval(correlation_id, session_id)

            elif payload_type == "awaiting_input":
                # If a message was queued while agents were running, use it now.
                queue = _get_queue(session_id)
                if queue:
                    resume_value = {"text": queue.popleft()}
                else:
                    loop = asyncio.get_event_loop()
                    future = loop.create_future()
                    _pending_inputs[session_id] = future
                    try:
                        resume_value = await future
                    finally:
                        # try/finally so a cancel/exception during await
                        # doesn't leave a stale entry in _pending_inputs
                        # that a later /message or /cancel would then
                        # misinterpret as a still-live waiter.
                        _pending_inputs.pop(session_id, None)

                # /close routes through here with resume_value={"close": True}.
                # Mark the runner so the natural loop exit below is treated
                # as a clean close rather than an unexpected END.
                if isinstance(resume_value, dict) and resume_value.get("close"):
                    close_requested = True

            else:
                logger.warning("Unknown interrupt payload type %r — ending session", payload_type)
                break

            result = await resume_session_with_value(session_id, resume_value)

        if close_requested:
            await event_bus.emit(session_id, {
                "type": "session_end",
                "session_id": session_id,
                "status": "closed",
            })
        else:
            # Graph reached END without the user closing. Most likely a
            # graph routing bug (e.g. user_closed state lingering from a
            # prior turn). Log loudly and do NOT close the project — the
            # user wants it to stay open for follow-ups. Tell the UI
            # we're idle so the spinner clears, but keep status active.
            logger.warning(
                "Session %s graph hit END without /close — leaving session active so "
                "the user can keep working.",
                session_id,
            )
            await event_bus.emit(session_id, {
                "type": "awaiting_user",
                "session_id": session_id,
                "last_response": "",
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
        heartbeat.cancel()
        _running_tasks.pop(session_id, None)
        _message_queues.pop(session_id, None)
        _close_requested_sessions.discard(session_id)
        for corr_id in _session_corr_ids(session_id):
            _unregister_approval(corr_id, session_id)
            # Mark the DB row expired so restart recovery doesn't replay
            # an interrupt for a session that is no longer running.
            try:
                await resolve_pending_approval(corr_id, status="expired")
            except Exception as exc:
                logger.warning("Could not mark approval %s expired: %s", corr_id, exc)
        _pending_inputs.pop(session_id, None)


def launch_session(
    session_id: str,
    task: str,
    model: str,
    approval_mode: str,
    project_path: str,
    max_turns: int = 20,
    resume: bool = False,
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
            resume=resume,
        ),
        name=f"session-{session_id}",
    )
    _running_tasks[session_id] = t
    return t


async def _relaunch_for_resume(session: dict) -> None:
    """Mark an idle session active again and re-attach a runner to it."""
    session_id = session["id"]
    await update_session_status(session_id, "active")
    launch_session(
        session_id=session_id,
        task=session.get("name", ""),
        model="claude:sonnet",  # unused on the resume path (graph state has it)
        approval_mode=session.get("approval_mode", "full-auto"),
        project_path=session.get("path", ""),
        resume=True,
    )


# ── REST endpoints ───────────────────────────────────────────────────────────


_WIN_PATH_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$", re.DOTALL)


def _is_wsl() -> bool:
    """True iff the backend is running inside WSL.

    Microsoft's published heuristic — read /proc/version and look for
    "Microsoft" or "WSL". Cached at import time would be wrong (tests
    monkeypatch it), so we read each time; the file is small and on a
    procfs read path so it's effectively free.
    """
    try:
        return any(
            tag in Path("/proc/version").read_text(errors="ignore")
            for tag in ("Microsoft", "microsoft", "WSL")
        )
    except OSError:
        return False


def _windows_to_wsl(raw: str) -> str:
    """Convert a Windows-style absolute path to its /mnt/<drive>/ equivalent.

    Examples:
      C:\\Users\\foo\\bar  →  /mnt/c/Users/foo/bar
      C:/Users/foo/bar     →  /mnt/c/Users/foo/bar
      D:\\projects         →  /mnt/d/projects

    Returns the input unchanged if it doesn't match the Windows-path
    shape — the caller dispatches to this only after `_WIN_PATH_RE`.
    """
    m = _WIN_PATH_RE.match(raw)
    if not m:
        return raw
    drive, rest = m.group(1).lower(), m.group(2)
    # Normalise the separators inside `rest` to forward slashes.
    rest = rest.replace("\\", "/")
    return f"/mnt/{drive}/{rest}".rstrip("/")


def _normalize_workspace_path(raw: str) -> Path:
    """Single funnel for all the path shapes the desktop can hand us.

    Order of operations:
      1. Strip surrounding whitespace.
      2. Expand `~` (already POSIX-safe; harmless on Windows-style input).
      3. If it looks like a Windows absolute path AND we're in WSL,
         rewrite to /mnt/<drive>/… so the backend can resolve it.
      4. Return as a `Path` — callers do the .exists() check.

    Windows paths on non-WSL backends are left alone; the .exists()
    check downstream will reject them with a clear message.
    """
    text = raw.strip()
    # Expanduser BEFORE the Windows-rewrite — the latter only matches
    # absolute drive paths, never `~/...`.
    expanded = str(Path(text).expanduser())
    if _WIN_PATH_RE.match(expanded) and _is_wsl():
        expanded = _windows_to_wsl(expanded)
    return Path(expanded)


def _resolve_workspace_path(raw: str | None, session_id: str) -> str:
    """Validate user-supplied workspace path, fall back to a session-local dir.

    The orchestrator's worktree pipeline assumes the path exists and is a
    directory. Without these checks, an empty / non-existent / file path
    surfaces as an opaque FileNotFoundError from inside uvloop when we
    later `cd` into it to run `git init`. We catch that here and return a
    clear 400 instead.

    The Tauri shell runs on Windows even when the backend lives in WSL,
    so its `@tauri-apps/plugin-dialog` folder picker returns Windows
    paths like ``C:\\Users\\…``. `_normalize_workspace_path` rewrites
    those into ``/mnt/c/…`` before we check `.exists()`.
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

    normalised = _normalize_workspace_path(raw)
    if not normalised.exists():
        # If the user gave us a Windows path but we're not on WSL, the
        # rewrite never fired and the existence check is about to fail.
        # Mention it in the error so the user knows what's up.
        hint = ""
        if _WIN_PATH_RE.match(raw.strip()) and not _is_wsl():
            hint = (
                " — Windows-style paths are only translated when the "
                "backend runs inside WSL."
            )
        raise HTTPException(
            status_code=400,
            detail=f"project_path does not exist: {normalised}{hint}",
        )
    if not normalised.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"project_path is not a directory: {normalised}",
        )
    return str(normalised)


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session_endpoint(req: CreateSessionRequest) -> CreateSessionResponse:
    session_id = uuid.uuid4().hex[:8]
    project_path = _resolve_workspace_path(req.project_path, session_id)

    await db_create_session(
        session_id,
        name=req.task[:80],
        path=project_path,
        approval_mode=req.approval_mode,
    )

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
    correlation_id = req.correlation_id
    if correlation_id is None:
        # Transitional: legacy clients didn't send a correlation_id. If
        # the session has exactly one in-flight approval, use it. If
        # there are 0 or >1, refuse — silently picking the "first" would
        # let two concurrent approvals clobber each other.
        corr_ids = _session_corr_ids(session_id)
        if len(corr_ids) == 1:
            correlation_id = corr_ids[0]
        elif not corr_ids:
            raise HTTPException(
                status_code=404,
                detail="No pending approval for this session",
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{len(corr_ids)} pending approvals for this session — "
                    "include correlation_id in the request body."
                ),
            )

    future = _pending_approvals.get(correlation_id)
    if future is None or future.done():
        raise HTTPException(
            status_code=404,
            detail=f"No live waiter for correlation_id {correlation_id!r}",
        )

    resume_value: dict = {"approved": req.approved}
    if req.team_composition:
        resume_value["team_composition"] = req.team_composition

    persisted = await resolve_pending_approval(
        correlation_id,
        status="approved" if req.approved else "rejected",
        response_payload=resume_value,
    )
    if not persisted:
        # Lost a race against another caller (or the runner's expire-on-
        # close path). The future is already done by now in the first
        # case, so the check above will normally have caught it.
        raise HTTPException(
            status_code=409,
            detail=f"Approval {correlation_id!r} was already resolved",
        )

    future.set_result(resume_value)
    # Defence-in-depth: the runner's own `finally:` will also unregister
    # this entry when its `await future` returns. Clearing here keeps the
    # in-memory map consistent even when callers manipulate it directly
    # (tests, Telegram callbacks that race against the runner exit).
    _unregister_approval(correlation_id, session_id)
    # Tell every connected WS client (and the ring buffer, which the
    # resume handshake replays) that the interrupt was consumed —
    # otherwise the approval card would pop right back when the
    # WebSocket reconnected and replayed the original interrupt event.
    await event_bus.emit(session_id, {
        "type": "interrupt_resolved",
        "session_id": session_id,
        "correlation_id": correlation_id,
        "approved": req.approved,
    })
    return {"ok": True, "correlation_id": correlation_id}


@router.get("/sessions/{session_id}/approvals")
async def list_session_approvals(session_id: str) -> dict:
    """Return every pending approval for this session.

    Used by clients reconnecting after a backend restart: when a session
    is re-opened, the client calls this to discover any approvals that
    were persisted but whose original WS interrupt event was missed.
    """
    rows = await list_pending_approvals(session_id=session_id)
    return {"session_id": session_id, "approvals": rows}


@router.post("/sessions/{session_id}/resume")
async def resume_session_endpoint(session_id: str) -> dict:
    """Re-attach a runner to a session left 'idle' by a backend restart.

    The graph resumes from its LangGraph checkpoint (usually a parked
    awaiting_input interrupt). No-op if a runner is already attached.
    """
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session_id in _running_tasks:
        return {"ok": True, "status": "already-running"}
    if session["status"] in ("closed", "failed"):
        raise HTTPException(
            status_code=409,
            detail=f"Session is {session['status']} — start a new session instead.",
        )
    await _relaunch_for_resume(session)
    return {"ok": True, "status": "resuming"}


@router.post("/sessions/{session_id}/message")
async def send_message(session_id: str, req: MessageRequest) -> dict:
    """Send a message to the orchestrator.

    If the graph is parked waiting for input → resume immediately.
    Otherwise (agents running, awaiting approval) → queue for next park.
    If no runner is attached (idle after a backend restart) → queue the
    message and auto-resume; the resumed graph consumes the queue at its
    parked awaiting_input interrupt.
    """
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")

    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

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

    # No live runner (backend restarted while this session was parked) —
    # re-attach one so the queued message is actually consumed instead of
    # sitting in an in-memory deque forever.
    if session_id not in _running_tasks and session["status"] not in ("closed", "failed"):
        await _relaunch_for_resume(session)
        return {"ok": True, "queued": True, "resumed": True}

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

    # Runner is alive but not parked. Defer the close — the runner picks
    # this flag up at the next interrupt boundary, so we don't mark the
    # session 'closed' while it's still emitting events (which used to
    # confuse the UI). If the runner has already exited (no task entry),
    # we can mark closed immediately.
    if session_id in _running_tasks:
        _close_requested_sessions.add(session_id)
        return {"ok": True, "status": "closing"}

    await update_session_status(session_id, "closed")
    await event_bus.emit(session_id, {
        "type": "session_closed",
        "session_id": session_id,
    })
    return {"ok": True, "status": "closed"}


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str) -> dict:
    """Stop a running orchestration mid-flight.

    Cancels the asyncio task driving the session, which:
      - cascades CancelledError into ClaudeCLIWorker.run
      - which catches it, calls kill() on the agent process group
      - which terminates any in-flight builder/reviewer subprocesses
      - and bubbles back to _session_runner's `except` branch, which
        emits session_error to the WebSocket so the UI clears the
        spinner.

    Different from /close: close politely waits for the current
    orchestrator turn to park. /cancel pulls the plug. Use when you
    realise you sent a wrong task and the agent is barrelling ahead.
    """
    task = _running_tasks.get(session_id)
    if task is None:
        # Nothing live — try to gracefully release any parked future.
        future = _pending_inputs.get(session_id)
        if future and not future.done():
            future.set_result({"close": True})
            _pending_inputs.pop(session_id, None)
        await update_session_status(session_id, "closed")
        await event_bus.emit(session_id, {
            "type": "session_closed",
            "session_id": session_id,
            "reason": "cancelled (no live task)",
        })
        return {"ok": True, "status": "closed"}

    # If the runner is parked at awaiting_input, take the clean shutdown
    # path: resolve the future with close=True so the runner exits via
    # its existing close_requested branch (emitting session_end) instead
    # of crashing into `except Exception` with a CancelledError. Then we
    # still emit session_cancelled so the UI knows it was a cancel, not
    # a graceful close.
    pi = _pending_inputs.get(session_id)
    parked_at_input = pi is not None and not pi.done()
    if parked_at_input:
        pi.set_result({"close": True})  # type: ignore[union-attr]
        _pending_inputs.pop(session_id, None)

    # Release any parked approval futures — these have to be force-
    # cancelled since there is no "close" semantic on an approval. The
    # runner's finally already marks rows expired; we mirror it here so
    # an immediate restart sees a consistent DB.
    for corr_id in _session_corr_ids(session_id):
        f = _pending_approvals.get(corr_id)
        if f and not f.done():
            f.cancel()
        _unregister_approval(corr_id, session_id)
        try:
            await resolve_pending_approval(corr_id, status="expired")
        except Exception as exc:
            logger.warning("Could not mark approval %s expired on cancel: %s", corr_id, exc)

    if parked_at_input:
        # Parked path: the runner is now winding down on its own. No
        # need to .cancel() — let it emit session_end naturally, then
        # we emit session_cancelled below so the UI can route the
        # status. Wait briefly for the runner to exit so callers don't
        # race a follow-up POST against the still-cleaning state.
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    else:
        # Runner is doing real work — propagate cancellation so the
        # worker's CancelledError handler can SIGTERM/SIGKILL the
        # subprocess via process group. Wait (bounded) so the HTTP
        # response only returns after the subprocess is gone.
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    await event_bus.emit(session_id, {
        "type": "session_cancelled",
        "session_id": session_id,
    })
    await update_session_status(session_id, "closed")
    return {"ok": True, "status": "cancelled"}
