"""Auto-recovery tests — verify crashed agents are detected and session resume works.

Two flavours of recovery:
  - Process-level (PID died) → detected at startup, marked 'crashed'
  - LangGraph checkpoint     → resume_session() picks up at the last interrupt
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio

from backend.persistence.db import get_conn, init_db
from backend.persistence.events import create_agent, create_session
from backend.persistence.recovery import (
    _pid_alive,
    detect_crashed_agents,
    mark_agents_crashed,
    run_startup_recovery,
)


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    path = tmp_path / "test.db"
    await init_db(path)
    return path


async def _set_pid(db_path: Path, agent_id: str, pid: int | None) -> None:
    async with get_conn(db_path) as conn:
        await conn.execute("UPDATE agents SET pid=? WHERE id=?", (pid, agent_id))
        await conn.commit()


# ── _pid_alive primitive ──────────────────────────────────────────────────────

def test_pid_alive_own_process() -> None:
    """The current process is always alive — sanity check."""
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_definitely_dead() -> None:
    """PID 999999 is unlikely to be a live process on a single-user machine."""
    assert _pid_alive(999_999_999) is False


# ── detect_crashed_agents ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detect_no_active_agents(db: Path) -> None:
    crashed = await detect_crashed_agents(db_path=db)
    assert crashed == []


@pytest.mark.asyncio
async def test_detect_agent_without_pid_is_crashed(db: Path) -> None:
    await create_session("s1", db_path=db)
    await create_agent("a1", "s1", role="Builder", model="claude:sonnet", worktree_path="/tmp/a1", db_path=db)
    # No PID set — recovery treats as crashed
    crashed = await detect_crashed_agents(db_path=db)
    assert len(crashed) == 1
    assert crashed[0]["id"] == "a1"


@pytest.mark.asyncio
async def test_detect_agent_with_live_pid_kept(db: Path) -> None:
    await create_session("s2", db_path=db)
    await create_agent("a2", "s2", role="Builder", model="claude:sonnet", worktree_path="/tmp/a2", db_path=db)
    await _set_pid(db, "a2", os.getpid())  # our own PID — alive

    crashed = await detect_crashed_agents(db_path=db)
    assert crashed == []


@pytest.mark.asyncio
async def test_detect_agent_with_dead_pid_marked(db: Path) -> None:
    await create_session("s3", db_path=db)
    await create_agent("a3", "s3", role="Builder", model="claude:sonnet", worktree_path="/tmp/a3", db_path=db)
    await _set_pid(db, "a3", 999_999_998)  # bogus PID

    crashed = await detect_crashed_agents(db_path=db)
    assert len(crashed) == 1
    assert crashed[0]["id"] == "a3"


# ── mark_agents_crashed ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mark_crashed_updates_status(db: Path) -> None:
    await create_session("s4", db_path=db)
    await create_agent("a4", "s4", role="Builder", model="claude:sonnet", worktree_path="/tmp/a4", db_path=db)

    await mark_agents_crashed(["a4"], db_path=db)
    async with get_conn(db) as conn:
        cursor = await conn.execute("SELECT status, ended_at FROM agents WHERE id=?", ("a4",))
        row = await cursor.fetchone()
    assert row["status"] == "crashed"
    assert row["ended_at"] is not None


@pytest.mark.asyncio
async def test_mark_crashed_marks_session_failed_when_all_done(db: Path) -> None:
    """If every agent in a session is non-active after the update, the session is failed."""
    await create_session("s5", db_path=db)
    await create_agent("a5", "s5", role="Builder", model="claude:sonnet", worktree_path="/tmp/a5", db_path=db)
    await mark_agents_crashed(["a5"], db_path=db)

    async with get_conn(db) as conn:
        cursor = await conn.execute("SELECT status FROM sessions WHERE id=?", ("s5",))
        row = await cursor.fetchone()
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_mark_crashed_leaves_session_active_if_other_agents_running(db: Path) -> None:
    """Don't fail the session if a sibling agent is still alive."""
    await create_session("s6", db_path=db)
    await create_agent("a6", "s6", role="Builder", model="claude:sonnet", worktree_path="/tmp/a6", db_path=db)
    await create_agent("a7", "s6", role="Tester",  model="claude:sonnet", worktree_path="/tmp/a7", db_path=db)
    await _set_pid(db, "a7", os.getpid())  # alive

    await mark_agents_crashed(["a6"], db_path=db)
    async with get_conn(db) as conn:
        cursor = await conn.execute("SELECT status FROM sessions WHERE id=?", ("s6",))
        row = await cursor.fetchone()
    # a7 is still active → session stays active
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_mark_crashed_empty_list_noop(db: Path) -> None:
    """An empty list must not crash or mutate anything."""
    await mark_agents_crashed([], db_path=db)  # should return cleanly


# ── run_startup_recovery (full pass) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_recovery_picks_up_dead_agents(db: Path) -> None:
    await create_session("s7", db_path=db)
    await create_agent("a8", "s7", role="Builder", model="claude:sonnet", worktree_path="/tmp/a8", db_path=db)
    await _set_pid(db, "a8", 999_999_997)

    recovered = await run_startup_recovery(db_path=db)
    assert len(recovered) == 1
    assert recovered[0]["id"] == "a8"

    async with get_conn(db) as conn:
        cursor = await conn.execute("SELECT status FROM agents WHERE id=?", ("a8",))
        row = await cursor.fetchone()
    assert row["status"] == "crashed"


@pytest.mark.asyncio
async def test_startup_recovery_idempotent(db: Path) -> None:
    """Running recovery twice should not re-flag already-crashed agents."""
    await create_session("s8", db_path=db)
    await create_agent("a9", "s8", role="Builder", model="claude:sonnet", worktree_path="/tmp/a9", db_path=db)
    await _set_pid(db, "a9", 999_999_996)

    first = await run_startup_recovery(db_path=db)
    second = await run_startup_recovery(db_path=db)
    assert len(first) == 1
    assert second == []  # already marked crashed → not 'active' anymore


# ── LangGraph-level resume ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_after_simulated_restart(tmp_path: Path) -> None:
    """A session parked at wait_for_user can be resumed in a fresh process.

    We approximate "fresh process" by closing the AsyncSqliteSaver between
    run_session and resume_session — that's the same lifecycle pattern
    `hive resume <id>` uses.
    """
    import time
    from unittest.mock import patch

    from backend.orchestrator.graph import (
        SessionInterrupt,
        resume_session,
        run_session,
    )

    db = tmp_path / "test.db"
    await init_db(db)

    async def fake_orchestrator(state: dict) -> dict:
        history = list(state.get("conversation_history") or [])
        message = state.get("pending_message") or state["task"]
        if not history or history[-1].get("content") != message:
            history.append({"role": "user", "content": message, "ts": time.time()})
        return {
            "team_composition": {"team": [], "confidence": 0.9, "rationale": "chat"},
            "last_response": "hi from recovery",
            "conversation_history": history,
        }

    with patch("backend.orchestrator.graph.orchestrator_node", fake_orchestrator):
        first = await run_session(
            session_id="sess-recover",
            agent_id="a1",
            task="hello",
            model="claude:sonnet",
            worktree_path=str(tmp_path),
            db_path=db,
        )

    assert isinstance(first, SessionInterrupt)
    assert first.payload["type"] == "awaiting_input"

    # Now simulate a restart: brand new resume_session call hits the same DB.
    with patch("backend.orchestrator.graph.orchestrator_node", fake_orchestrator):
        resumed = await resume_session("sess-recover", db_path=db)

    # The checkpoint surfaces the same interrupt — session lives across restart.
    assert isinstance(resumed, SessionInterrupt)
    assert resumed.payload["type"] == "awaiting_input"


# ── pending_approvals persistence (invariant #5) ──────────────────────────


@pytest.mark.asyncio
async def test_pending_approval_round_trip(db: Path) -> None:
    """Approval rows must survive across a backend restart so a re-opened
    session can replay the interrupt to its reconnecting clients."""
    from backend.persistence.events import (
        create_pending_approval,
        get_pending_approval,
        list_pending_approvals,
        resolve_pending_approval,
    )

    await create_session("sess-pa", path="/tmp/x", db_path=db)
    await create_pending_approval(
        correlation_id="cid-1",
        session_id="sess-pa",
        agent_id="orchestrator",
        request_payload={"type": "team_approval", "confidence": 0.42},
        db_path=db,
    )

    # Survives "restart": close + reopen the DB and read back.
    row = await get_pending_approval("cid-1", db_path=db)
    assert row is not None
    assert row["status"] == "pending"
    assert row["request_payload"]["confidence"] == 0.42

    pending = await list_pending_approvals(session_id="sess-pa", db_path=db)
    assert len(pending) == 1
    assert pending[0]["correlation_id"] == "cid-1"


@pytest.mark.asyncio
async def test_resolve_pending_approval_is_idempotent(db: Path) -> None:
    """Second resolve on the same row must return False (already settled)
    so racing callers can't both fire a waiter."""
    from backend.persistence.events import (
        create_pending_approval,
        resolve_pending_approval,
    )

    await create_session("sess-id", path="/tmp/y", db_path=db)
    await create_pending_approval(
        correlation_id="cid-id",
        session_id="sess-id",
        agent_id="orchestrator",
        request_payload={},
        db_path=db,
    )
    first = await resolve_pending_approval(
        "cid-id", status="approved", response_payload={"approved": True}, db_path=db
    )
    second = await resolve_pending_approval(
        "cid-id", status="rejected", response_payload={"approved": False}, db_path=db
    )
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_parallel_pending_approvals_for_one_session(db: Path) -> None:
    """Two approval rows for one session, both pending, distinct ids."""
    from backend.persistence.events import (
        create_pending_approval,
        list_pending_approvals,
    )

    await create_session("sess-multi", path="/tmp/z", db_path=db)
    await create_pending_approval("a", "sess-multi", "agent-1", {}, db_path=db)
    await create_pending_approval("b", "sess-multi", "agent-2", {}, db_path=db)

    rows = await list_pending_approvals(session_id="sess-multi", db_path=db)
    assert {r["correlation_id"] for r in rows} == {"a", "b"}
