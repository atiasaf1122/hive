"""LangGraph orchestrator graph — Phase 3: approval modes.

Graph topology:
    START → plan → approval → [abort | spawn → run_workers → review] → END

approval_node interrupts if:
  - approval_mode is 'checkpoint' or 'manual', OR
  - approval_mode is 'full-auto' but Planner confidence < 0.7

Resuming: call resume_session_with_value(session_id, {"approved": True/False})
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from backend.orchestrator.nodes.planner import _parse_team_composition, plan_team
from backend.orchestrator.nodes.reviewer import ReviewReport, review_and_merge, summarize_results
from backend.orchestrator.nodes.spawner import SpawnPlan, SpawnedAgent, spawn_agents
from backend.orchestrator.state import AgentResult, GraphState
from backend.persistence.db import DB_PATH
from backend.persistence.events import (
    create_agent,
    create_session,
    update_agent_status,
    update_session_status,
    write_cost,
    write_event,
)
from backend.skills.injector import build_skill_context
from backend.skills.registry import search as search_skills
from backend.workers.base import EventType, WorkerConfig
from backend.workers.claude_cli import ClaudeCLIWorker
from backend.workers.ollama import OllamaWorker

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 3


async def _emit_to_ws(session_id: str, payload: dict) -> None:
    """Best-effort emit to WebSocket event bus — never raises."""
    try:
        from backend.api.event_bus import emit  # lazy import avoids circular dep
        await emit(session_id, payload)
    except Exception:
        pass


@dataclass
class SessionInterrupt:
    """Returned by run_session / resume_session when the graph is paused for approval."""
    session_id: str
    payload: dict


# ── graph nodes ──────────────────────────────────────────────────────────────

async def plan_node(state: GraphState) -> dict:
    """Call the Planner LLM to decide team composition."""
    composition = await plan_team(
        task=state["task"],
        session_id=state["session_id"],
    )
    result = {
        "team_composition": {
            "team": [
                {"role": m.role, "model": m.model, "count": m.count, "passive": m.passive}
                for m in composition.team
            ],
            "confidence": composition.confidence,
            "rationale": composition.rationale,
        }
    }
    await _emit_to_ws(state["session_id"], {
        "type": "plan_complete",
        "session_id": state["session_id"],
        "team_composition": result["team_composition"],
    })
    return result


async def approval_node(state: GraphState) -> dict:
    """Interrupt for human review of the proposed team composition.

    Triggers when:
      - mode is 'checkpoint' or 'manual' (always ask)
      - mode is 'full-auto' and Planner confidence < 0.7
    """
    mode = state.get("approval_mode") or "full-auto"
    comp = state.get("team_composition") or {}
    confidence = float(comp.get("confidence", 1.0))

    low_confidence = confidence < 0.7
    needs_approval = mode in ("checkpoint", "manual") or (mode == "full-auto" and low_confidence)

    if not needs_approval:
        return {}

    response = interrupt({
        "type": "team_approval",
        "team_composition": comp,
        "confidence": confidence,
        "reason": "low_confidence" if low_confidence else "approval_mode",
    })

    if not response.get("approved", True):
        return {"approval_rejected": True}

    # Allow the user to supply a modified composition
    modified = response.get("team_composition")
    if modified:
        return {"team_composition": modified}
    return {}


def _route_after_approval(state: GraphState) -> str:
    return "abort" if state.get("approval_rejected") else "spawn"


async def abort_node(state: GraphState) -> dict:
    """Terminal node when the user rejects the team proposal."""
    return {
        "result": AgentResult(
            agent_id="orchestrator",
            status="cancelled",
            text_output="",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            error="Task cancelled by user",
        )
    }


async def spawn_node(state: GraphState) -> dict:
    """Create git worktrees and register agents for the planned team."""
    raw = state.get("team_composition") or {}
    import json
    composition = _parse_team_composition(json.dumps(raw))

    project_path = state.get("project_path") or state.get("worktree_path") or os.getcwd()

    plan = await spawn_agents(
        session_id=state["session_id"],
        task=state["task"],
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


async def run_workers_node(state: GraphState) -> dict:
    """Run all active agents in parallel (up to MAX_CONCURRENT at a time)."""
    plan_dict = state.get("spawn_plan") or {}
    active = [_dict_to_agent(a) for a in plan_dict.get("active_agents", [])]

    if not active:
        active = [SpawnedAgent(
            agent_id=state["agent_id"],
            role="worker",
            model=state["model"],
            worktree_path=state.get("worktree_path", os.getcwd()),
        )]

    task = state["task"]
    session_id = state["session_id"]
    max_turns = state.get("max_turns", 20)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def _run_one(agent: SpawnedAgent) -> tuple[str, AgentResult]:
        async with semaphore:
            return agent.agent_id, await _execute_worker(agent, task, session_id, max_turns)

    pairs = await asyncio.gather(*[_run_one(a) for a in active])
    return {"worker_results": {aid: res for aid, res in pairs}}


async def review_node(state: GraphState) -> dict:
    """Merge worktrees and produce review report."""
    plan_dict = state.get("spawn_plan")
    results: dict[str, AgentResult] = state.get("worker_results") or {}

    if not plan_dict:
        single = state.get("result")
        return {"review_report": {"notes": [], "success": True}, "result": single}

    plan = SpawnPlan(
        session_id=plan_dict["session_id"],
        project_path=plan_dict["project_path"],
        active_agents=[_dict_to_agent(a) for a in plan_dict.get("active_agents", [])],
        passive_agents=[_dict_to_agent(a) for a in plan_dict.get("passive_agents", [])],
    )

    report = await review_and_merge(plan=plan, results=results)

    total_in = sum(r["input_tokens"] for r in results.values())
    total_out = sum(r["output_tokens"] for r in results.values())
    total_cost = sum(r["cost_usd"] for r in results.values())
    combined_text = "\n\n".join(
        f"[{r['agent_id']}]\n{r['text_output']}"
        for r in results.values()
        if r["text_output"]
    )
    # Only mark failed if nothing merged at all — conflicts with partial success = completed
    all_failed = len(report.failed_agents) > 0 and len(report.merged) == 0
    final_status = "failed" if all_failed else "completed"
    await update_session_status(state["session_id"], final_status)

    combined_result = AgentResult(
        agent_id="orchestrator",
        status=final_status,
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
    }


# ── graph wiring ─────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    builder = StateGraph(GraphState)
    builder.add_node("plan", plan_node)
    builder.add_node("approval", approval_node)
    builder.add_node("abort", abort_node)
    builder.add_node("spawn", spawn_node)
    builder.add_node("run_workers", run_workers_node)
    builder.add_node("review", review_node)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "approval")
    builder.add_conditional_edges(
        "approval",
        _route_after_approval,
        {"spawn": "spawn", "abort": "abort"},
    )
    builder.add_edge("abort", END)
    builder.add_edge("spawn", "run_workers")
    builder.add_edge("run_workers", "review")
    builder.add_edge("review", END)
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
    """Run a session. Returns AgentResult on completion, SessionInterrupt if paused."""
    await create_session(session_id, name=task[:80], db_path=db_path)

    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as checkpointer:
        graph = build_graph().compile(checkpointer=checkpointer)

        initial: GraphState = {
            "session_id": session_id,
            "task": task,
            "project_path": worktree_path,
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
            agent_id=agent_id, status="failed", text_output="",
            input_tokens=0, output_tokens=0, cost_usd=0.0,
            error="No result returned from graph",
        )


async def resume_session_with_value(
    session_id: str,
    resume_value: dict,
    db_path: Path = DB_PATH,
) -> AgentResult | SessionInterrupt:
    """Resume an interrupted session with a user decision.

    resume_value examples:
      {"approved": True}
      {"approved": False}
      {"approved": True, "team_composition": {...}}   # modified plan
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
            agent_id=session_id, status="failed", text_output="",
            input_tokens=0, output_tokens=0, cost_usd=0.0,
            error="No result returned after resume",
        )


async def resume_session(
    session_id: str, db_path: Path = DB_PATH
) -> AgentResult | SessionInterrupt | None:
    """Resume a session from checkpoint. Returns SessionInterrupt if still waiting for approval."""
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as checkpointer:
        graph = build_graph().compile(checkpointer=checkpointer)
        thread_config = {"configurable": {"thread_id": session_id}}
        state = await graph.aget_state(thread_config)
        if state is None or not state.values:
            return None

        # If paused at an interrupt, surface it directly without re-running
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


# ── internal helpers ─────────────────────────────────────────────────────────

async def _auto_commit_worktree(worktree_path: str, agent_id: str) -> bool:
    """Stage and commit all changes in the worktree after the agent completes."""
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


async def _execute_worker(
    agent: SpawnedAgent,
    task: str,
    session_id: str,
    max_turns: int,
) -> AgentResult:
    """Run a single worker and collect its result, persisting all events."""
    role_task = f"[{agent.role}] {task}"

    skill_context = ""
    try:
        relevant = await search_skills(role_task, top_k=3)
        skill_context = build_skill_context(relevant)
        if relevant:
            logger.info("Injecting %d skill(s) for agent %s", len(relevant), agent.agent_id)
    except Exception as exc:
        logger.debug("Skill search skipped: %s", exc)

    worker = ClaudeCLIWorker() if agent.model.startswith("claude:") else OllamaWorker()
    config = WorkerConfig(
        agent_id=agent.agent_id,
        session_id=session_id,
        model=agent.model,
        worktree_path=agent.worktree_path,
        max_turns=max_turns,
        system_prompt=skill_context,
    )

    text_parts: list[str] = []
    result = AgentResult(
        agent_id=agent.agent_id, status="completed", text_output="",
        input_tokens=0, output_tokens=0, cost_usd=0.0, error=None,
    )

    try:
        async for event in worker.run(role_task, config):
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
            })

            if event.type == EventType.TEXT_DELTA and event.text:
                text_parts.append(event.text)
            elif event.type == EventType.COST:
                result["input_tokens"] = event.input_tokens or 0
                result["output_tokens"] = event.output_tokens or 0
                result["cost_usd"] = event.cost_usd or 0.0
                try:
                    await write_cost(session_id, agent.agent_id,
                                     result["input_tokens"], result["output_tokens"], result["cost_usd"])
                except Exception:
                    pass
            elif event.type == EventType.AGENT_ERROR:
                result["status"] = "failed"
                result["error"] = event.error
    except Exception as exc:
        logger.exception("Worker %s crashed", agent.agent_id)
        result["status"] = "failed"
        result["error"] = str(exc)

    result["text_output"] = "".join(text_parts)
    await _auto_commit_worktree(agent.worktree_path, agent.agent_id)
    final_status = result["status"]
    await update_agent_status(agent.agent_id, final_status)
    return result


def _agent_to_dict(a: SpawnedAgent) -> dict:
    return {
        "agent_id": a.agent_id, "role": a.role, "model": a.model,
        "worktree_path": a.worktree_path, "passive": a.passive, "branch": a.branch,
    }


def _dict_to_agent(d: dict) -> SpawnedAgent:
    return SpawnedAgent(
        agent_id=d["agent_id"], role=d["role"], model=d["model"],
        worktree_path=d["worktree_path"], passive=d.get("passive", False),
        branch=d.get("branch", ""),
    )
