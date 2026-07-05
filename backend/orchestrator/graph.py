"""LangGraph orchestrator — orchestrator-first multi-turn model.

A session is a long-lived conversation. The Orchestrator is the user's
permanent contact and decides per message whether to answer directly or
spawn agents. After each turn the graph parks at `wait_for_user`, an
interrupt() gate that resumes when the user sends another message — or
ends when the user closes the project.

Graph topology:

    START → orchestrator ─┬─ respond ─────────────────────────────► wait_for_user
                          └─ approval ─┬─ abort ──────────────────► wait_for_user
                                       └─ spawn → run_workers
                                                       → review ──► wait_for_user

    wait_for_user (interrupt) ─┬─ user sent message  ─► orchestrator (loop)
                               └─ user closed        ─► END
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from backend.orchestrator.nodes.planner import (
    _parse_composition_dict,
    _parse_team_composition,
    orchestrate,
)
from backend.orchestrator.nodes.reviewer import ReviewReport, llm_review, review_and_merge
from backend.orchestrator.nodes.spawner import SpawnPlan, SpawnedAgent, spawn_agents
from backend.orchestrator.state import AgentResult, GraphState
from backend.persistence.db import DB_PATH
from backend.safety.circuit_breaker import BreakerState, default_registry as breaker_registry
from backend.safety.hard_stops import DEFAULTS as HARD_STOPS, check as check_hard_stops
from backend.safety.overrides import effective_limits as effective_safety_limits
from backend.validation.trust import record_completion as record_trust_completion
from backend.persistence.events import (
    create_agent,
    create_session,
    update_agent_status,
    update_session_status,
    write_cost,
    write_event,
)
from backend.skills.injector import build_skill_context
from backend.skills.registry import hybrid_search
from backend.workers.base import EventType, HiveEvent, WorkerConfig
from backend.workers.claude_cli import ClaudeCLIWorker
from backend.workers.ollama import OllamaWorker

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 3


async def _emit_to_ws(session_id: str, payload: dict) -> None:
    """Best-effort emit to WebSocket event bus — never raises.

    The graph must never block on a slow/dead client. We log the failure at
    debug level so it's recoverable from `hive start --verbose` without
    spamming normal output.
    """
    try:
        from backend.api.event_bus import emit  # lazy import avoids circular dep
        await emit(session_id, payload)
    except Exception as exc:
        logger.debug("WS emit failed for %s: %s", session_id, exc)


@dataclass
class SessionInterrupt:
    """Returned by run_session / resume_session when the graph is paused."""
    session_id: str
    payload: dict


# ── orchestrator + respond ───────────────────────────────────────────────────

async def orchestrator_node(state: GraphState) -> dict:
    """One orchestrator turn: read pending message, decide chat-vs-spawn."""
    message = state.get("pending_message") or state["task"]
    history = list(state.get("conversation_history") or [])

    # Ensure the message is at the end of the history (it always should be —
    # wait_for_user appends it on resume — but the initial task hasn't passed
    # through wait_for_user, so append it here if missing).
    if not history or history[-1].get("role") != "user" or history[-1].get("content") != message:
        history.append({"role": "user", "content": message, "ts": time.time()})

    await _emit_to_ws(state["session_id"], {
        "type": "orchestrator_thinking",
        "session_id": state["session_id"],
        "message": message,
    })

    # D3: compaction — when the history grows past the threshold (or every
    # N turns), collapse older turns into a CURRENT STATE doc. Pruned turns
    # are persisted in the compaction event; nothing is lost.
    state_doc = state.get("state_doc") or ""
    turns_since = int(state.get("turns_since_compaction") or 0) + 1
    from backend.orchestrator.compaction import (
        KEEP_LAST_TURNS,
        build_state_doc,
        should_compact,
    )
    if len(history) > KEEP_LAST_TURNS and should_compact(history, turns_since):
        pruned = history[:-KEEP_LAST_TURNS]
        new_doc = await build_state_doc(
            history, state_doc, state["task"], session_id=state["session_id"])
        if new_doc:
            try:
                await write_event(HiveEvent(
                    type=EventType.COMPACTION,
                    agent_id="orchestrator", session_id=state["session_id"],
                    raw_payload={"state_doc": new_doc,
                                 "pruned_turns": len(pruned),
                                 "pruned": pruned},
                ))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Event write failed: %s", exc)
            await _emit_to_ws(state["session_id"], {
                "type": "context_compacted",
                "session_id": state["session_id"],
                "pruned_turns": len(pruned),
            })
            history = history[-KEEP_LAST_TURNS:]
            state_doc = new_doc
            turns_since = 0
            logger.info("Compacted %d turns into state doc for %s",
                        len(pruned), state["session_id"])

    decision = await orchestrate(
        message=message,
        session_id=state["session_id"],
        history=history[:-1],  # don't include the current message twice
        project_path=state.get("project_path") or state.get("worktree_path"),
        state_doc=state_doc,
    )

    composition_dict = _composition_to_dict(decision.composition)

    # D2: plan-quality gate — score before spawn/approval so the result is
    # visible in the approval modal. One automatic revision round max; the
    # gate fails open, the user always decides.
    if decision.has_active_team:
        from backend.orchestrator.plan_gate import score_plan

        check = await score_plan(composition_dict, message, session_id=state["session_id"])
        if not check.passed:
            logger.warning("Plan gate flagged (score %d): %s", check.score, check.issues)
            revised = await orchestrate(
                message=(
                    f"{message}\n\n[Plan reviewer feedback — revise your team plan "
                    f"to fix these issues, or justify keeping it]\n"
                    + "\n".join(f"- {i}" for i in check.issues)
                ),
                session_id=state["session_id"],
                history=history[:-1],
                project_path=state.get("project_path") or state.get("worktree_path"),
            )
            if revised.has_active_team:
                decision = revised
                composition_dict = _composition_to_dict(decision.composition)
                check = await score_plan(composition_dict, message, session_id=state["session_id"])
        composition_dict["plan_check"] = check.to_dict()
        await _emit_to_ws(state["session_id"], {
            "type": "plan_check",
            "session_id": state["session_id"],
            **check.to_dict(),
        })

    await _emit_to_ws(state["session_id"], {
        "type": "orchestrator_decision",
        "session_id": state["session_id"],
        "response": decision.response,
        "team_composition": composition_dict,
        "has_team": decision.has_active_team,
    })

    return {
        "team_composition": composition_dict,
        "last_response": decision.response,
        "conversation_history": history,
        "state_doc": state_doc,
        "turns_since_compaction": turns_since,
    }


def _composition_to_dict(composition) -> dict:
    return {
        "team": [
            {
                "role": m.role, "model": m.model, "count": m.count,
                "passive": m.passive, "subtask": m.subtask,
                "files_hint": m.files_hint, "max_turns": m.max_turns,
                "mcp_servers": getattr(m, "mcp_servers", []),
            }
            for m in composition.team
        ],
        "confidence": composition.confidence,
        "rationale": composition.rationale,
    }


def _route_after_orchestrator(state: GraphState) -> str:
    comp = state.get("team_composition") or {}
    has_active = any(
        not m.get("passive") and int(m.get("count", 0)) > 0
        for m in comp.get("team", [])
    )
    return "approval" if has_active else "respond"


async def respond_node(state: GraphState) -> dict:
    """Orchestrator answered without spawning agents — record + park."""
    response = state.get("last_response", "") or ""
    history = list(state.get("conversation_history") or [])
    history.append({"role": "assistant", "content": response, "ts": time.time()})

    await _emit_to_ws(state["session_id"], {
        "type": "orchestrator_response",
        "session_id": state["session_id"],
        "text": response,
    })

    return {
        "conversation_history": history,
        "result": AgentResult(
            agent_id="orchestrator",
            status="completed",
            text_output=response,
            input_tokens=0, output_tokens=0, cost_usd=0.0,
            error=None,
        ),
    }


# ── approval ─────────────────────────────────────────────────────────────────

async def approval_node(state: GraphState) -> dict:
    """Interrupt for human review of the proposed team composition."""
    mode = state.get("approval_mode") or "full-auto"
    comp = state.get("team_composition") or {}
    confidence = float(comp.get("confidence", 1.0))

    low_confidence = confidence < 0.7
    plan_check = comp.get("plan_check") or {}
    plan_flagged = plan_check and not plan_check.get("passed", True)
    needs_approval = (
        mode in ("checkpoint", "manual")
        or (mode == "full-auto" and (low_confidence or plan_flagged))
    )

    if not needs_approval:
        return {}

    response = interrupt({
        "type": "team_approval",
        "team_composition": comp,
        "confidence": confidence,
        "plan_check": plan_check or None,
        "reason": (
            "plan_check" if plan_flagged
            else ("low_confidence" if low_confidence else "approval_mode")
        ),
    })

    if not response.get("approved", True):
        return {"approval_rejected": True}

    modified = response.get("team_composition")
    if modified:
        return {"team_composition": modified}
    return {}


def _route_after_approval(state: GraphState) -> str:
    return "abort" if state.get("approval_rejected") else "spawn"


async def abort_node(state: GraphState) -> dict:
    """User rejected the proposed team — record + park."""
    history = list(state.get("conversation_history") or [])
    history.append({
        "role": "assistant",
        "content": "Task cancelled by user.",
        "ts": time.time(),
    })
    return {
        "approval_rejected": False,  # clear flag so future turns are unblocked
        "conversation_history": history,
        "result": AgentResult(
            agent_id="orchestrator",
            status="cancelled",
            text_output="",
            input_tokens=0, output_tokens=0, cost_usd=0.0,
            error="Task cancelled by user",
        ),
    }


# ── spawn → run_workers → review ─────────────────────────────────────────────

async def spawn_node(state: GraphState) -> dict:
    """Create git worktrees and register agents for the planned team.

    Phase 10 wiring (Section 6 hard stops): before we even create
    worktrees, check the global hard-stop limits against the proposed
    fan-out + the tokens we've already burned this turn. A hit pauses
    the spawn and surfaces the reason to the chat thread as a system
    message — same shape as agent failures so the UI is consistent.
    """
    raw = state.get("team_composition") or {}
    import json
    composition = _parse_team_composition(json.dumps(raw))

    proposed_agents = sum(m.count for m in composition.team if not m.passive)
    tokens_used = sum(
        int(r.get("input_tokens", 0)) + int(r.get("output_tokens", 0))
        for r in (state.get("worker_results") or {}).values()
    )
    # Per-session safety overrides layer on top of HARD_STOPS — see
    # `backend/safety/overrides.py`. If the user hasn't set any, we get
    # the build-time defaults back.
    try:
        session_limits = await effective_safety_limits(state["session_id"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("safety override lookup failed; using HARD_STOPS: %s", exc)
        session_limits = HARD_STOPS
    violation = check_hard_stops(
        concurrent_agents=proposed_agents,
        tokens_used=tokens_used,
        limits=session_limits,
    )
    if violation is not None:
        history = list(state.get("conversation_history") or [])
        history.append({
            "role": "system",
            "content": (
                f"Spawn paused by safety limit "
                f"({violation.limit_name}={violation.observed}, "
                f"threshold={violation.threshold}). "
                f"{violation.rationale}"
            ),
            "ts": time.time(),
        })
        await _emit_to_ws(state["session_id"], {
            "type": "safety_hard_stop",
            "session_id": state["session_id"],
            "limit": violation.limit_name,
            "observed": violation.observed,
            "threshold": violation.threshold,
            "rationale": violation.rationale,
        })
        return {
            "conversation_history": history,
            "approval_rejected": True,
        }

    project_path = state.get("project_path") or state.get("worktree_path") or os.getcwd()
    pending = state.get("pending_message") or state["task"]

    plan = await spawn_agents(
        session_id=state["session_id"],
        task=pending,
        composition=composition,
        project_path=project_path,
    )

    plan_dict = {
        "session_id": plan.session_id,
        "project_path": plan.project_path,
        "active_agents": [_agent_to_dict(a) for a in plan.active_agents],
        "passive_agents": [_agent_to_dict(a) for a in plan.passive_agents],
    }
    await _emit_to_ws(state["session_id"], {
        "type": "spawn_complete",
        "session_id": state["session_id"],
        "agents": plan_dict["active_agents"],
    })
    return {"spawn_plan": plan_dict}


def _build_agent_prompt(agent: SpawnedAgent, goal: str, pending: str) -> str:
    """Compose one agent's OWN prompt from its brief (B1).

    Before B1 every agent received the identical `[role] task` prompt — three
    Builders did the same work three times. Now each agent gets the project
    goal for context plus its specific subtask. When the planner didn't emit
    a subtask (legacy plans, fallback team) the current request IS the
    subtask, preserving old behaviour for single-agent teams.
    """
    parts = [f"You are the {agent.role} agent in a HIVE multi-agent team."]
    parts.append(f"## Project goal (context)\n{goal}")
    if pending and pending.strip() != goal.strip():
        parts.append(f"## Current request\n{pending}")
    parts.append(f"## Your subtask\n{agent.subtask or pending}")
    if agent.files_hint:
        parts.append("## Files in your scope\n" + "\n".join(f"- {f}" for f in agent.files_hint))
    if agent.mcp_servers:
        # C5 lesson: without this, an equipped agent doesn't know its MCP
        # tools exist and reinstalls the capability from scratch via Bash.
        from backend.mcp.catalog import get_spec
        lines = []
        for sid in agent.mcp_servers:
            spec = get_spec(sid)
            label = spec.label if spec else sid
            lines.append(f"- {sid}: {label}")
        parts.append(
            "## Equipment\nYou are equipped with these MCP servers — their "
            "tools are already connected (find them with tool search if not "
            "immediately visible). Use THEM; do NOT install or re-build these "
            "capabilities yourself:\n" + "\n".join(lines)
        )
    parts.append(
        "Stay strictly within your subtask — other agents own the rest of the "
        "work, and duplicating it creates merge conflicts. Commit nothing "
        "yourself; HIVE auto-commits your worktree when you finish."
    )
    return "\n\n".join(parts)


async def run_workers_node(state: GraphState) -> dict:
    """Run all active agents in parallel (up to MAX_CONCURRENT at a time).

    Each agent receives its own subtask prompt and per-agent max_turns (B1).
    """
    plan_dict = state.get("spawn_plan") or {}
    active = [_dict_to_agent(a) for a in plan_dict.get("active_agents", [])]
    if not active:
        # Can't happen through the normal spawn path (the planner auto-floors
        # a Builder), but guard against a malformed checkpoint.
        logger.warning("run_workers reached with no active agents — skipping")
        return {"worker_results": {}}

    pending = state.get("pending_message") or state["task"]
    goal = state["task"]
    session_id = state["session_id"]
    default_max_turns = state.get("max_turns", 20)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def _run_one(agent: SpawnedAgent) -> tuple[str, AgentResult]:
        prompt = _build_agent_prompt(agent, goal=goal, pending=pending)
        # D1.4: conservative lesson retrieval — a HIGH similarity bar means
        # zero injections is the normal outcome. Max 3, same-project first.
        try:
            from backend.lessons.store import (
                record_application,
                render_lessons_section,
                retrieve_lessons,
            )
            lessons = await retrieve_lessons(
                f"{agent.role}: {agent.subtask or pending}",
                project_path=state.get("project_path"),
            )
            if lessons:
                prompt += "\n\n" + render_lessons_section(lessons)
                for lesson in lessons:
                    await record_application(session_id, lesson.id, agent.agent_id)
                logger.info("Injected %d lesson(s) for %s", len(lessons), agent.agent_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Lesson retrieval skipped: %s", exc)
        max_turns = agent.max_turns or default_max_turns
        async with semaphore:
            return agent.agent_id, await _execute_worker(agent, prompt, session_id, max_turns)

    pairs = await asyncio.gather(*[_run_one(a) for a in active])
    return {"worker_results": {aid: res for aid, res in pairs}}


async def review_node(state: GraphState) -> dict:
    """Merge worktrees, produce review report, park session for next turn."""
    plan_dict = state.get("spawn_plan")
    results: dict[str, AgentResult] = state.get("worker_results") or {}
    history = list(state.get("conversation_history") or [])

    if not plan_dict:
        history.append({
            "role": "assistant",
            "content": state.get("last_response", "") or "(no work performed)",
            "ts": time.time(),
        })
        return {
            "review_report": {"notes": [], "success": True},
            "conversation_history": history,
        }

    plan = SpawnPlan(
        session_id=plan_dict["session_id"],
        project_path=plan_dict["project_path"],
        active_agents=[_dict_to_agent(a) for a in plan_dict.get("active_agents", [])],
        passive_agents=[_dict_to_agent(a) for a in plan_dict.get("passive_agents", [])],
    )

    report = await review_and_merge(plan=plan, results=results)

    # B6: LLM escalation — ONLY when the mechanical pass hit a merge
    # conflict or a worker failed validation. Clean merges (the common
    # case) never pay for an Opus call.
    any_validation_failed = any(
        r.get("validation_passed") is False for r in results.values()
    )
    if report.conflicts or any_validation_failed:
        await _emit_to_ws(state["session_id"], {
            "type": "llm_review_started",
            "session_id": state["session_id"],
            "conflicts": len(report.conflicts),
            "validation_failures": any_validation_failed,
        })
        notes = await llm_review(plan=plan, report=report, results=results)
        report.notes.extend(notes)
        try:
            await write_event(HiveEvent(
                type=EventType.REVIEW_LLM,
                agent_id="reviewer", session_id=state["session_id"],
                raw_payload={"notes": notes,
                             "conflicts": len(report.conflicts),
                             "validation_failures": any_validation_failed},
            ))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Event write failed: %s", exc)

    total_in = sum(r["input_tokens"] for r in results.values())
    total_out = sum(r["output_tokens"] for r in results.values())
    total_cost = sum(r["cost_usd"] for r in results.values())
    # B3: history gets each worker's COMPACT summary, not its raw output —
    # the full transcript lives in the events table. Falls back to a
    # truncated excerpt for results that carry no summary (legacy shapes).
    combined_text = "\n\n".join(
        f"[{r['agent_id']}]\n{r.get('summary') or r['text_output'][:1200]}"
        for r in results.values()
        if r.get("summary") or r["text_output"]
    )
    all_failed = len(report.failed_agents) > 0 and len(report.merged) == 0
    turn_status = "failed" if all_failed else "completed"

    summary = state.get("last_response", "") or ""
    if combined_text:
        summary = (summary + "\n\n" if summary else "") + combined_text

    final_message = summary or f"Turn {turn_status}."
    history.append({
        "role": "assistant",
        "content": final_message,
        "ts": time.time(),
    })

    # Also push the message via the WebSocket — review_node mutates
    # conversation_history server-side, but the frontend store
    # reconstructs chat from WS events, not from /history. Without this
    # explicit emit, the spinner spins forever and the user never sees
    # the combined worker output until they refresh.
    await _emit_to_ws(state["session_id"], {
        "type": "orchestrator_response",
        "session_id": state["session_id"],
        "text": final_message,
    })

    combined_result = AgentResult(
        agent_id="orchestrator",
        status=turn_status,
        text_output=combined_text,
        input_tokens=total_in,
        output_tokens=total_out,
        cost_usd=total_cost,
        error="; ".join(report.notes) if report.notes and not report.success else None,
    )
    return {
        "review_report": {
            "notes": report.notes,
            "success": report.success,
            "total_commits_merged": report.total_commits_merged,
            "failed_agents": report.failed_agents,
        },
        "result": combined_result,
        "conversation_history": history,
        # Update last_response so wait_for_user's awaiting_user event
        # carries the combined summary (a safety net for clients that
        # only consume awaiting_user, e.g. a reconnect mid-review).
        "last_response": final_message,
        # Reset per-turn slots so the next turn starts clean
        "spawn_plan": None,
        "worker_results": {},
    }


# ── wait_for_user (the multi-turn loop hinge) ────────────────────────────────

async def wait_for_user_node(state: GraphState) -> dict:
    """Park until the user sends another message or closes the project."""
    if state.get("user_closed"):
        return {}

    await _emit_to_ws(state["session_id"], {
        "type": "awaiting_user",
        "session_id": state["session_id"],
        "last_response": state.get("last_response", ""),
    })

    response = interrupt({
        "type": "awaiting_input",
        "session_id": state["session_id"],
        "last_response": state.get("last_response", ""),
    })

    if response.get("close"):
        db_path = Path(state.get("db_path") or DB_PATH)
        await update_session_status(state["session_id"], "closed", db_path=db_path)
        await _emit_to_ws(state["session_id"], {
            "type": "session_closed",
            "session_id": state["session_id"],
        })
        return {"user_closed": True, "pending_message": ""}

    text = (response.get("text") or "").strip()
    if not text:
        # Empty message → loop back to wait without recording anything
        return {}

    history = list(state.get("conversation_history") or [])
    history.append({"role": "user", "content": text, "ts": time.time()})
    return {
        "pending_message": text,
        "conversation_history": history,
        # Clear any stale turn state
        "team_composition": None,
        "approval_rejected": False,
    }


def _route_after_wait(state: GraphState) -> str:
    return END if state.get("user_closed") else "orchestrator"


# ── graph wiring ─────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    builder = StateGraph(GraphState)
    builder.add_node("orchestrator", orchestrator_node)
    builder.add_node("respond", respond_node)
    builder.add_node("approval", approval_node)
    builder.add_node("abort", abort_node)
    builder.add_node("spawn", spawn_node)
    builder.add_node("run_workers", run_workers_node)
    builder.add_node("review", review_node)
    builder.add_node("wait_for_user", wait_for_user_node)

    builder.add_edge(START, "orchestrator")
    builder.add_conditional_edges(
        "orchestrator",
        _route_after_orchestrator,
        {"respond": "respond", "approval": "approval"},
    )
    builder.add_edge("respond", "wait_for_user")
    builder.add_conditional_edges(
        "approval",
        _route_after_approval,
        {"spawn": "spawn", "abort": "abort"},
    )
    builder.add_edge("abort", "wait_for_user")
    builder.add_edge("spawn", "run_workers")
    builder.add_edge("run_workers", "review")
    builder.add_edge("review", "wait_for_user")
    builder.add_conditional_edges(
        "wait_for_user",
        _route_after_wait,
        {"orchestrator": "orchestrator", END: END},
    )
    return builder


# ── public API ───────────────────────────────────────────────────────────────

async def run_session(
    session_id: str,
    agent_id: str,
    task: str,
    model: str,
    worktree_path: str,
    max_turns: int = 20,
    db_path: Path = DB_PATH,
    approval_mode: str = "full-auto",
) -> AgentResult | SessionInterrupt:
    """Start a session. Returns AgentResult only if the user closes immediately;
    normally returns SessionInterrupt as the graph parks for the next message."""
    # path= and approval_mode= persisted so session recovery, Telegram
    # routing, and the UI list view all see the right values. Skipping
    # either silently stores '' / 'full-auto', which previously surfaced
    # as "project disappeared" / "wrong approval mode" after restart.
    await create_session(
        session_id,
        name=task[:80],
        path=worktree_path,
        approval_mode=approval_mode,
        db_path=db_path,
    )

    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as checkpointer:
        graph = build_graph().compile(checkpointer=checkpointer)

        initial: GraphState = {
            "session_id": session_id,
            "task": task,
            "project_path": worktree_path,
            "db_path": str(db_path),
            "agent_id": agent_id,
            "model": model,
            "worktree_path": worktree_path,
            "max_turns": max_turns,
            "team_composition": None,
            "spawn_plan": None,
            "worker_results": {},
            "review_report": None,
            "result": None,
            "messages": [],
            "approval_mode": approval_mode,
            "approval_rejected": False,
            "conversation_history": [],
            "state_doc": "",
            "turns_since_compaction": 0,
            "pending_message": task,
            "last_response": "",
            "user_closed": False,
        }

        thread_config = {"configurable": {"thread_id": session_id}}
        final: dict = {}
        async for chunk in graph.astream(initial, thread_config, stream_mode="values"):
            if "__interrupt__" in chunk:
                payload = chunk["__interrupt__"][0].value
                return SessionInterrupt(session_id=session_id, payload=payload)
            final = chunk

        result = final.get("result")
        return result or AgentResult(
            agent_id=agent_id, status="completed", text_output="",
            input_tokens=0, output_tokens=0, cost_usd=0.0,
            error=None,
        )


async def resume_session_with_value(
    session_id: str,
    resume_value: dict,
    db_path: Path = DB_PATH,
) -> AgentResult | SessionInterrupt:
    """Resume an interrupted session with a user decision or new message.

    resume_value shapes:
      {"approved": True/False, "team_composition": {...}}   # for team_approval interrupts
      {"text": "next user message"}                          # for awaiting_input interrupts
      {"close": True}                                        # for awaiting_input → close
    """
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as checkpointer:
        graph = build_graph().compile(checkpointer=checkpointer)
        thread_config = {"configurable": {"thread_id": session_id}}

        final: dict = {}
        async for chunk in graph.astream(
            Command(resume=resume_value), thread_config, stream_mode="values"
        ):
            if "__interrupt__" in chunk:
                payload = chunk["__interrupt__"][0].value
                return SessionInterrupt(session_id=session_id, payload=payload)
            final = chunk

        result = final.get("result")
        return result or AgentResult(
            agent_id=session_id, status="completed", text_output="",
            input_tokens=0, output_tokens=0, cost_usd=0.0,
            error=None,
        )


async def resume_session(
    session_id: str, db_path: Path = DB_PATH
) -> AgentResult | SessionInterrupt | None:
    """Resume a session from its last checkpoint without supplying a value.

    Returns the current interrupt if the session is parked.
    """
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as checkpointer:
        graph = build_graph().compile(checkpointer=checkpointer)
        thread_config = {"configurable": {"thread_id": session_id}}
        state = await graph.aget_state(thread_config)
        if state is None or not state.values:
            return None

        tasks_with_interrupts = [t for t in state.tasks if t.interrupts]
        if tasks_with_interrupts:
            payload = tasks_with_interrupts[0].interrupts[0].value
            return SessionInterrupt(session_id=session_id, payload=payload)

        final: dict = {}
        async for chunk in graph.astream(None, thread_config, stream_mode="values"):
            if "__interrupt__" in chunk:
                payload = chunk["__interrupt__"][0].value
                return SessionInterrupt(session_id=session_id, payload=payload)
            final = chunk

        return final.get("result")


async def get_conversation_history(
    session_id: str, db_path: Path = DB_PATH
) -> list[dict]:
    """Read the orchestrator conversation history from the latest checkpoint."""
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as checkpointer:
        graph = build_graph().compile(checkpointer=checkpointer)
        thread_config = {"configurable": {"thread_id": session_id}}
        state = await graph.aget_state(thread_config)
        if state is None or not state.values:
            return []
        return list(state.values.get("conversation_history") or [])


# ── internal helpers ─────────────────────────────────────────────────────────

async def _auto_commit_worktree(worktree_path: str, agent_id: str) -> bool:
    """Stage and commit all changes in the worktree after the agent completes.

    Sets a local git identity fallback if none is present so commits never
    fail with "Author identity unknown" (the snake-game stall bug).
    """
    try:
        status_proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await status_proc.communicate()
        if not stdout.strip():
            return False

        # Ensure a per-repo identity exists so the commit never stalls.
        await _ensure_worktree_identity(worktree_path)

        add_proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await add_proc.communicate()

        commit_proc = await asyncio.create_subprocess_exec(
            "git", "commit", "-m", f"hive: agent {agent_id} output",
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await commit_proc.communicate()
        if commit_proc.returncode != 0:
            logger.warning("Auto-commit failed for %s: %s", agent_id, stderr.decode())
            return False

        logger.info("Auto-committed worktree for %s", agent_id)
        return True
    except Exception as exc:
        logger.warning("Auto-commit error for %s: %s", agent_id, exc)
        return False


async def _ensure_worktree_identity(worktree_path: str) -> None:
    """Set local git user.name/user.email in the worktree if absent."""
    async def _has(key: str) -> bool:
        proc = await asyncio.create_subprocess_exec(
            "git", "config", "--get", key,
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        return bool(out.strip())

    if await _has("user.name") and await _has("user.email"):
        return
    for key, value in (("user.name", "HIVE"), ("user.email", "hive@localhost")):
        proc = await asyncio.create_subprocess_exec(
            "git", "config", key, value,
            cwd=worktree_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()


async def _summarize_worker_run(
    events: list, session_id: str, agent: SpawnedAgent, prompt: str
):
    """One Haiku call collapsing a worker's event stream (B3).

    Isolated so tests can patch it; production path builds a session-scoped
    HaikuCaller (budgeted, cost-logged) over the summarizer runner.
    """
    from backend.llm.haiku import HaikuCaller
    from backend.summarizer.runner import summarize_events

    caller = HaikuCaller(
        worker=ClaudeCLIWorker(),
        session_id=session_id,
        agent_id_prefix=f"summarizer-{agent.agent_id}",
    )
    return await summarize_events(
        events,
        haiku_caller=caller,
        task_description=agent.subtask or prompt[:300],
    )


async def _execute_worker(
    agent: SpawnedAgent,
    prompt: str,
    session_id: str,
    max_turns: int,
) -> AgentResult:
    """Run a single worker and collect its result, persisting all events.

    Phase 10 wiring (Section 6.2 circuit breakers):
      - Before spawning, ask the per-worker breaker `can_attempt()`. An
        OPEN breaker → instant-fail the agent with a clear message so the
        Reviewer doesn't try to merge nothing.
      - After the run, record success/failure on the breaker AND on the
        worker_trust_scores table.
    """
    breaker = breaker_registry.get(agent.model or agent.role)
    if not breaker.can_attempt():
        wait = int(breaker.time_until_close())
        msg = (
            f"Circuit breaker is OPEN for {breaker.worker_id} after "
            f"{breaker.consecutive_failures} consecutive failures. "
            f"Skipping this agent; try again in ~{wait}s."
        )
        await _emit_to_ws(session_id, {
            "type": "safety_breaker_open",
            "session_id": session_id,
            "agent_id": agent.agent_id,
            "worker_id": breaker.worker_id,
            "time_until_close_seconds": wait,
        })
        return AgentResult(
            agent_id=agent.agent_id, status="failed", text_output="",
            input_tokens=0, output_tokens=0, cost_usd=0.0, error=msg,
            failure_origin="infrastructure",
        )

    # Skill retrieval keys off the agent's own brief — the subtask is a far
    # sharper query than the shared session task ever was. B5: uses the
    # hybrid ranker (semantic + BM25 + tag overlap) instead of plain cosine.
    skill_query = f"[{agent.role}] {agent.subtask or prompt[:300]}"
    skill_context = ""
    try:
        hits = await hybrid_search(skill_query, top_k=3)
        relevant = [h.skill for h in hits]
        skill_context = build_skill_context(relevant)
        if relevant:
            logger.info("Injecting %d skill(s) for agent %s", len(relevant), agent.agent_id)
    except Exception as exc:
        logger.debug("Skill search skipped: %s", exc)

    # C2: per-agent MCP servers — preflight, render, write the config file.
    # Preflight failures fail the spawn FAST with a named requirement instead
    # of letting the claude CLI die cryptically mid-run.
    mcp_config_path: str | None = None
    if agent.mcp_servers and agent.model.startswith("claude:"):
        from backend.mcp.catalog import get_spec, preflight, render_mcp_config
        from backend.persistence.db import HIVE_DIR

        missing: list[str] = []
        for sid in agent.mcp_servers:
            spec = get_spec(sid)
            if spec is None:
                missing.append(f"unknown MCP server {sid!r}")
                continue
            missing.extend(f"{sid}: {m}" for m in preflight(spec))
            if not missing:
                # D0.3: prove the server actually starts (initialize
                # handshake) before an agent run pays for it. Cached per
                # args-hash, so only the session's first spawn waits.
                from backend.mcp.doctor import check_server
                ok, detail = await check_server(
                    spec, agent_id=agent.agent_id, worktree=agent.worktree_path,
                )
                if not ok:
                    missing.append(f"{sid}: server failed doctor check — {detail}")
        if missing:
            msg = "MCP preflight failed — " + "; ".join(missing)
            logger.error("%s (agent=%s)", msg, agent.agent_id)
            error_event = HiveEvent(
                type=EventType.AGENT_ERROR, agent_id=agent.agent_id,
                session_id=session_id, error=msg, origin="infrastructure",
            )
            try:
                await write_event(error_event)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Event write failed: %s", exc)
            await _emit_to_ws(session_id, {
                "type": "mcp_preflight_failed",
                "session_id": session_id,
                "agent_id": agent.agent_id,
                "missing": missing,
            })
            await update_agent_status(agent.agent_id, "failed")
            breaker.record_failure()
            return AgentResult(
                agent_id=agent.agent_id, status="failed", text_output="",
                input_tokens=0, output_tokens=0, cost_usd=0.0, error=msg,
                failure_origin="infrastructure",
            )

        cfg = render_mcp_config(agent.mcp_servers, agent.agent_id, agent.worktree_path)
        cfg_dir = HIVE_DIR / "mcp-configs"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file = cfg_dir / f"{session_id}-{agent.agent_id}.json"
        # Outside the worktree on purpose: auto-commit would otherwise sweep
        # the config (and any expanded tokens) into the merge history.
        import json as _json
        cfg_file.write_text(_json.dumps(cfg, indent=2))
        mcp_config_path = str(cfg_file)

        # C4: visibility — the event stream shows what each agent carried.
        attach_event = HiveEvent(
            type=EventType.MCP_ATTACHED, agent_id=agent.agent_id,
            session_id=session_id,
            raw_payload={"servers": list(agent.mcp_servers)},
        )
        try:
            await write_event(attach_event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Event write failed: %s", exc)
        await _emit_to_ws(session_id, {
            "type": "mcp_servers_attached",
            "session_id": session_id,
            "agent_id": agent.agent_id,
            "servers": list(agent.mcp_servers),
        })

    # B2: mint or reuse this logical agent's claude conversation uuid so a
    # re-spawned agent resumes with its context instead of starting amnesiac.
    claude_session_id: str | None = None
    resume_claude = False
    if agent.model.startswith("claude:"):
        try:
            from backend.persistence.events import get_or_create_claude_session
            claude_session_id, resume_claude = await get_or_create_claude_session(agent.agent_id)
        except Exception as exc:
            logger.warning("claude session lookup failed for %s: %s", agent.agent_id, exc)

    worker = ClaudeCLIWorker() if agent.model.startswith("claude:") else OllamaWorker()
    config = WorkerConfig(
        agent_id=agent.agent_id,
        session_id=session_id,
        model=agent.model,
        worktree_path=agent.worktree_path,
        max_turns=max_turns,
        system_prompt=skill_context,
        claude_session_id=claude_session_id,
        resume_claude_session=resume_claude,
        mcp_config_path=mcp_config_path,
    )

    text_parts: list[str] = []
    final_text: str | None = None    # populated when TEXT_DONE arrives
    collected_events: list = []      # B3: fed to the Haiku summarizer post-run
    result = AgentResult(
        agent_id=agent.agent_id, status="completed", text_output="",
        input_tokens=0, output_tokens=0, cost_usd=0.0, error=None,
    )

    try:
        async for event in worker.run(prompt, config):
            collected_events.append(event)
            try:
                await write_event(event)
            except Exception as exc:
                logger.debug("Event write failed: %s", exc)

            await _emit_to_ws(session_id, {
                "type": event.type,
                "agent_id": event.agent_id,
                "session_id": session_id,
                "ts": str(event.ts) if event.ts else None,
                "text": event.text,
                "error": event.error,
                "input_tokens": event.input_tokens,
                "output_tokens": event.output_tokens,
                "cost_usd": event.cost_usd,
                # Tool detail — without these the drill-down panel
                # could only show the word "tool" 50 times in a row.
                "tool_name": event.tool_name,
                "tool_input": event.tool_input,
                "tool_use_id": event.tool_use_id,
                "tool_result": event.tool_result,
                "tool_result_error": event.tool_result_error,
            })

            if event.type == EventType.AGENT_START and event.pid:
                # Persist the worker's OS PID so startup recovery can check
                # process liveness instead of assuming every restart-orphaned
                # agent crashed (agents.pid was previously never written).
                try:
                    await update_agent_status(agent.agent_id, "active", pid=event.pid)
                except Exception as exc:
                    logger.warning("PID write failed for %s: %s", agent.agent_id, exc)
            elif event.type == EventType.TEXT_DELTA and event.text:
                text_parts.append(event.text)
            elif event.type == EventType.TEXT_DONE and event.text:
                # Consolidated assistant message — supersedes the
                # partial deltas so we don't store the same paragraph
                # twice in `text_output`.
                final_text = event.text
            elif event.type == EventType.COST:
                result["input_tokens"] = event.input_tokens or 0
                result["output_tokens"] = event.output_tokens or 0
                result["cost_usd"] = event.cost_usd or 0.0
                try:
                    await write_cost(session_id, agent.agent_id,
                                     result["input_tokens"], result["output_tokens"], result["cost_usd"])
                except Exception as exc:
                    logger.warning("Cost write failed for %s: %s", agent.agent_id, exc)
            elif event.type == EventType.AGENT_ERROR:
                result["status"] = "failed"
                result["error"] = event.error
                result["failure_origin"] = event.origin or "unknown"
    except Exception as exc:
        logger.exception("Worker %s crashed", agent.agent_id)
        result["status"] = "failed"
        result["error"] = str(exc)
        result["failure_origin"] = "infrastructure"  # HIVE's own code raised

    result["text_output"] = final_text if final_text is not None else "".join(text_parts)
    await _auto_commit_worktree(agent.worktree_path, agent.agent_id)
    final_status = result["status"]

    # B3: collapse the run into a compact Haiku summary. The full transcript
    # is already persisted in the events table; only the summary enters the
    # orchestrator's conversation history. Degrades to a truncated raw
    # excerpt if the summarizer errors — a summarizer outage must never
    # fail the turn.
    completion_report = None
    result["summary"] = ""
    if collected_events and agent.model.startswith("claude:"):
        try:
            tiered = await _summarize_worker_run(
                collected_events, session_id, agent, prompt,
            )
            result["summary"] = tiered.standard or tiered.tldr
            completion_report = tiered.detailed
        except Exception as exc:  # noqa: BLE001
            logger.warning("Summarizer failed for %s: %s", agent.agent_id, exc)
    if not result["summary"]:
        raw = result["text_output"].strip()
        result["summary"] = raw[:1200] + ("…" if len(raw) > 1200 else "")

    # B4: deterministic validation — trust scores now mean "claims checked
    # against the worktree's git state", not "process exited 0". Only the
    # file validators run (TestRun/PackageInstall need evidence sources
    # HIVE doesn't collect since the Phase A command-audit deletion).
    result["validation_passed"] = None
    result["validation_findings"] = []
    if final_status == "completed" and completion_report is not None:
        try:
            from backend.validation.context import collect_git_context
            from backend.validation.validators import (
                FileCreationValidator,
                FileDeletionValidator,
                FileModificationValidator,
                validate_report_async,
            )

            ctx = await collect_git_context(agent.worktree_path)
            vres = await validate_report_async(
                completion_report, ctx,
                validators=[
                    FileModificationValidator(),
                    FileCreationValidator(),
                    FileDeletionValidator(),
                ],
            )
            result["validation_passed"] = vres.passed
            result["validation_findings"] = [
                f.detail for f in vres.findings if not f.ok
            ]
            if not vres.passed:
                logger.warning(
                    "Validation FAILED for %s: %s",
                    agent.agent_id, result["validation_findings"],
                )
                # D1 evidence: persist the diagnosis — lessons are distilled
                # from these events at session close.
                try:
                    await write_event(HiveEvent(
                        type=EventType.VALIDATION_FAILED,
                        agent_id=agent.agent_id, session_id=session_id,
                        origin="agent",
                        raw_payload={"findings": result["validation_findings"]},
                    ))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Event write failed: %s", exc)
                await _emit_to_ws(session_id, {
                    "type": "validation_failed",
                    "session_id": session_id,
                    "agent_id": agent.agent_id,
                    "findings": result["validation_findings"],
                })
                result["summary"] += (
                    "\n\n⚠ Validation failed: "
                    + "; ".join(result["validation_findings"][:3])
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Validation errored for %s: %s", agent.agent_id, exc)

    await update_agent_status(agent.agent_id, final_status)

    # Section 6.2 — breaker bookkeeping.
    if final_status == "completed":
        breaker.record_success()
    else:
        breaker.record_failure()

    # Section 5.5 — trust score per worker model. "Passed" now requires the
    # validators to agree, not just a clean exit; an unvalidated completion
    # (no structured report available) counts as passed to avoid punishing
    # chat-only roles that produce no file claims. D0.2: failures are only
    # charged to the worker when the fault is the agent's own output —
    # validation failures are origin='agent'; stream/HIVE failures carry
    # whatever origin the failure path assigned.
    passed = final_status == "completed" and result["validation_passed"] is not False
    if result.get("validation_passed") is False:
        result["failure_origin"] = "agent"
    origin = result.get("failure_origin") or ("agent" if passed else "unknown")
    try:
        await record_trust_completion(
            breaker.worker_id, passed_validation=passed, origin=origin,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Trust record failed for %s: %s", breaker.worker_id, exc)

    return result


def _agent_to_dict(a: SpawnedAgent) -> dict:
    return {
        "agent_id": a.agent_id, "role": a.role, "model": a.model,
        "worktree_path": a.worktree_path, "passive": a.passive, "branch": a.branch,
        "subtask": a.subtask, "files_hint": a.files_hint, "max_turns": a.max_turns,
        "mcp_servers": a.mcp_servers,
    }


def _dict_to_agent(d: dict) -> SpawnedAgent:
    return SpawnedAgent(
        agent_id=d["agent_id"], role=d["role"], model=d["model"],
        worktree_path=d["worktree_path"], passive=d.get("passive", False),
        branch=d.get("branch", ""),
        subtask=d.get("subtask", ""), files_hint=d.get("files_hint"),
        max_turns=d.get("max_turns"),
        mcp_servers=d.get("mcp_servers") or [],
    )
