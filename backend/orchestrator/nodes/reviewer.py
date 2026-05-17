"""Reviewer node — monitors worker results and merges worktrees.

The Reviewer runs after all active workers finish. It:
1. Collects results from all workers
2. Merges each agent's worktree branch into the main branch (in order)
3. Handles conflicts by reporting them back to the orchestrator
4. Produces a ReviewReport with quality notes

Uses claude:sonnet during development (Opus in production).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from backend.orchestrator.nodes.spawner import SpawnedAgent, SpawnPlan
from backend.orchestrator.state import AgentResult
from backend.worktrees.manager import MergeResult, WorktreeManager

logger = logging.getLogger(__name__)


@dataclass
class ReviewReport:
    session_id: str
    merged: list[MergeResult] = field(default_factory=list)
    conflicts: list[MergeResult] = field(default_factory=list)
    failed_agents: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.conflicts) == 0 and len(self.failed_agents) == 0

    @property
    def total_commits_merged(self) -> int:
        return sum(m.commits_merged for m in self.merged)


async def review_and_merge(
    plan: SpawnPlan,
    results: dict[str, AgentResult],
    main_branch: str = "main",
) -> ReviewReport:
    """Merge all completed agent worktrees and produce a ReviewReport."""
    report = ReviewReport(session_id=plan.session_id)
    manager = WorktreeManager(
        session_id=plan.session_id,
        project_path=plan.project_path,
    )

    for agent in plan.active_agents:
        result = results.get(agent.agent_id)

        if result is None or result["status"] == "failed":
            error = result["error"] if result else "no result"
            logger.warning("Agent %s failed — skipping merge: %s", agent.agent_id, error)
            report.failed_agents.append(agent.agent_id)
            report.notes.append(f"{agent.role} ({agent.agent_id}) failed: {error}")
            continue

        if result["status"] == "completed":
            merge_result = await manager.merge_to_main(
                agent_id=agent.agent_id,
                main_branch=main_branch,
            )
            if merge_result.success:
                report.merged.append(merge_result)
                if merge_result.commits_merged > 0:
                    report.notes.append(
                        f"{agent.role} ({agent.agent_id}): merged {merge_result.commits_merged} commit(s)"
                    )
            else:
                report.conflicts.append(merge_result)
                report.notes.append(
                    f"{agent.role} ({agent.agent_id}): CONFLICT in {merge_result.conflict_files}"
                )
                logger.warning(
                    "Merge conflict for agent=%s files=%s",
                    agent.agent_id, merge_result.conflict_files,
                )

    # Clean up worktrees after merge
    await manager.remove_session_worktrees()

    logger.info(
        "Review complete | merged=%d conflicts=%d failed=%d",
        len(report.merged), len(report.conflicts), len(report.failed_agents),
    )
    return report


def summarize_results(results: dict[str, AgentResult], report: ReviewReport) -> str:
    """Build a human-readable summary of what all agents did."""
    lines = []
    for agent_id, result in results.items():
        status_icon = "✓" if result["status"] == "completed" else "✗"
        cost_str = f"${result['cost_usd']:.4f}" if result["cost_usd"] else "$0.00"
        lines.append(
            f"  {status_icon} {agent_id}: {result['input_tokens']}→{result['output_tokens']} tokens, {cost_str}"
        )

    lines.append("")
    for note in report.notes:
        lines.append(f"  • {note}")

    total_cost = sum(r["cost_usd"] for r in results.values())
    total_in = sum(r["input_tokens"] for r in results.values())
    total_out = sum(r["output_tokens"] for r in results.values())
    lines.append(f"\n  Total: {total_in}→{total_out} tokens, ${total_cost:.4f}")

    return "\n".join(lines)
