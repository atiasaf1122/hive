"""Spawner node — creates worktrees and launches workers in parallel.

Uses LangGraph's Send API to fan out to one node call per agent.
Enforces a concurrency cap (default 3 simultaneous active agents).
Passive agents (e.g. Debugger) are registered but not started immediately.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from backend.orchestrator.nodes.planner import TeamComposition, TeamMember
from backend.persistence.events import create_agent, create_session
from backend.worktrees.manager import WorktreeManager

logger = logging.getLogger(__name__)

MAX_CONCURRENT_WORKERS = int(3)  # configurable, hard cap at 7 per HIVE_BUILD_PLAN


@dataclass
class SpawnedAgent:
    agent_id: str
    role: str
    model: str
    worktree_path: str
    passive: bool = False
    branch: str = ""
    # B1: per-agent brief — each agent runs ITS OWN prompt, not the shared task.
    subtask: str = ""
    files_hint: list[str] | None = None
    max_turns: int | None = None  # None → inherit the session default


@dataclass
class SpawnPlan:
    """The output of the Spawner node — ready-to-run agents."""
    session_id: str
    project_path: str
    active_agents: list[SpawnedAgent] = field(default_factory=list)
    passive_agents: list[SpawnedAgent] = field(default_factory=list)

    @property
    def all_agents(self) -> list[SpawnedAgent]:
        return self.active_agents + self.passive_agents


async def spawn_agents(
    session_id: str,
    task: str,
    composition: TeamComposition,
    project_path: str,
    max_concurrent: int = MAX_CONCURRENT_WORKERS,
) -> SpawnPlan:
    """Create worktrees for all agents and register them in the DB.

    Expands each TeamMember with count>1 into individual SpawnedAgent entries.
    Active agents beyond max_concurrent will be queued (handled by the runner).
    """
    manager = WorktreeManager(session_id=session_id, project_path=project_path)
    plan = SpawnPlan(session_id=session_id, project_path=project_path)

    # Flatten team composition into individual agent slots
    all_members: list[tuple[TeamMember, int]] = []
    for member in composition.team:
        for i in range(member.count):
            all_members.append((member, i))

    # Create worktrees concurrently (but not more than cap at once)
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _create_one(member: TeamMember, index: int) -> SpawnedAgent | None:
        agent_id = f"{member.role.lower()}-{session_id[:6]}-{index}"
        branch = f"hive/{session_id}/{agent_id}"
        async with semaphore:
            try:
                wt_path = await manager.create(agent_id=agent_id, branch_name=branch)
            except RuntimeError as exc:
                logger.error("Failed to create worktree for %s: %s", agent_id, exc)
                return None

        await create_agent(
            agent_id=agent_id,
            session_id=session_id,
            role=member.role,
            model=member.model,
            worktree_path=str(wt_path),
        )

        return SpawnedAgent(
            agent_id=agent_id,
            role=member.role,
            model=member.model,
            worktree_path=str(wt_path),
            passive=member.passive,
            branch=branch,
            subtask=getattr(member, "subtask", ""),
            files_hint=getattr(member, "files_hint", None),
            max_turns=getattr(member, "max_turns", None),
        )

    results = await asyncio.gather(*[_create_one(m, i) for m, i in all_members])

    for agent in results:
        if agent is None:
            continue
        if agent.passive:
            plan.passive_agents.append(agent)
        else:
            plan.active_agents.append(agent)

    logger.info(
        "SpawnPlan ready | session=%s | active=%d | passive=%d",
        session_id, len(plan.active_agents), len(plan.passive_agents),
    )
    return plan
