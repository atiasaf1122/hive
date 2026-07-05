"""Reviewer node — monitors worker results and merges worktrees.

The Reviewer runs after all active workers finish. It:
1. Collects results from all workers
2. Merges each agent's worktree branch into the main branch (in order)
3. Handles conflicts by reporting them back to the orchestrator
4. Produces a ReviewReport with quality notes

The mechanical git merge is the default fast path. An Opus LLM pass
(`llm_review`) runs ONLY on the rare, high-value cases: a merge conflict
or a worker that failed validation (B6) — clean merges never pay for an
LLM call.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from backend.models import OPUS_MODEL
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


async def llm_review(
    plan: SpawnPlan,
    report: ReviewReport,
    results: dict[str, AgentResult],
    model: str = OPUS_MODEL,
    max_turns: int = 10,
) -> list[str]:
    """Opus escalation pass — merge conflicts and validation failures only.

    Runs in the PROJECT directory (post-merge state). Conflicted agent
    branches survive the aborted merge, so the reviewer can re-attempt the
    merge and resolve conflicts in place. Returns notes for the report;
    never raises (an LLM outage must not fail the turn — the mechanical
    report already carries the conflict facts).
    """
    from backend.workers.base import EventType, WorkerConfig
    from backend.workers.claude_cli import ClaudeCLIWorker

    parts = [
        "You are the Reviewer for a HIVE multi-agent session. The mechanical "
        "merge pass has finished; you are called ONLY because something "
        "needs judgment. Work in the current repository.",
    ]
    if report.conflicts:
        lines = [
            f"- branch `{c.branch}` (agent {c.agent_id}): conflicts in {', '.join(c.conflict_files) or 'unknown files'}"
            for c in report.conflicts
        ]
        parts.append(
            "## Merge conflicts (each merge was aborted; the branch still exists)\n"
            + "\n".join(lines)
            + "\n\nRe-attempt each merge and resolve the conflicts sensibly — "
            "prefer combining both sides' intent; keep the build working."
        )
    failed_validation = {
        aid: r.get("validation_findings", [])
        for aid, r in results.items()
        if r.get("validation_passed") is False
    }
    if failed_validation:
        lines = [
            f"- {aid}: " + "; ".join(findings[:3])
            for aid, findings in failed_validation.items()
        ]
        parts.append(
            "## Validation failures (worker claims not backed by git evidence)\n"
            + "\n".join(lines)
            + "\n\nVerify what was actually delivered; fix trivial gaps, "
            "flag anything that needs a re-run."
        )
    parts.append(
        "Finish with a SHORT report (max 6 lines): what you merged/fixed, "
        "what remains broken, and what the orchestrator should do next."
    )
    prompt = "\n\n".join(parts)

    config = WorkerConfig(
        agent_id=f"reviewer-{plan.session_id}",
        session_id=plan.session_id,
        model=model,
        worktree_path=plan.project_path,
        max_turns=max_turns,
    )
    try:
        worker = ClaudeCLIWorker()
        text_parts: list[str] = []
        final_text: str | None = None
        async for event in worker.run(prompt, config):
            if event.type == EventType.TEXT_DELTA and event.text:
                text_parts.append(event.text)
            elif event.type == EventType.TEXT_DONE and event.text:
                final_text = event.text
            elif event.type == EventType.AGENT_ERROR:
                logger.warning("LLM review errored: %s", event.error)
                return [f"LLM review failed: {event.error}"]
        text = (final_text if final_text is not None else "".join(text_parts)).strip()
        return [f"LLM review ({model}): {text}"] if text else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM review crashed: %s", exc)
        return [f"LLM review crashed: {exc}"]


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
