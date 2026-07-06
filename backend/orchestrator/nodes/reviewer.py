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

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

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
        agent_id=f"llm-review-{plan.session_id}",
        session_id=plan.session_id,
        model=model,
        worktree_path=plan.project_path,
        max_turns=max_turns,
    )
    try:
        from backend.persistence.events import write_cost

        worker = ClaudeCLIWorker()
        text_parts: list[str] = []
        final_text: str | None = None
        async for event in worker.run(prompt, config):
            if event.type == EventType.TEXT_DELTA and event.text:
                text_parts.append(event.text)
            elif event.type == EventType.TEXT_DONE and event.text:
                final_text = event.text
            elif event.type == EventType.COST:
                # E0.2 — the Opus review call was invisible to cost
                # accounting (Phase D flag); log it like any worker.
                try:
                    await write_cost(plan.session_id, config.agent_id,
                                     event.input_tokens or 0,
                                     event.output_tokens or 0,
                                     event.cost_usd or 0.0)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("LLM review cost write failed: %s", exc)
            elif event.type == EventType.AGENT_ERROR:
                logger.warning("LLM review errored: %s", event.error)
                return [f"LLM review failed: {event.error}"]
        text = (final_text if final_text is not None else "".join(text_parts)).strip()
        return [f"LLM review ({model}): {text}"] if text else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM review crashed: %s", exc)
        return [f"LLM review crashed: {exc}"]


# ── F3: salvage review — don't discard a failed agent's committed work ───────

@dataclass
class SalvageVerdict:
    agent_id: str
    branch: str
    action: str          # "merge" | "discard" | "conflict"
    reasoning: str
    commits: int


_SALVAGE_MIN_CHANGED_LINES = 5    # don't pay Opus to review a trivial branch


async def salvage_failed_agents(
    plan: SpawnPlan,
    results: dict[str, AgentResult],
    session_id: str,
    main_branch: str = "main",
    model: str = OPUS_MODEL,
) -> list[SalvageVerdict]:
    """For each agent that FAILED but left committed work on its branch, ask
    Opus whether to merge or discard it, then act. Branches survive worktree
    removal (shared object store), so this runs after review_and_merge.

    Cost-guarded: only branches with ≥1 commit AND a non-trivial diff pay
    for an Opus call (the palette Tester died at the finish line in D and
    its finished work was silently dropped — this is the fix).
    """
    from backend.worktrees.manager import _run as _git

    project = Path(plan.project_path)
    verdicts: list[SalvageVerdict] = []

    for agent in plan.active_agents:
        result = results.get(agent.agent_id)
        if result is not None and result["status"] != "failed":
            continue
        branch = f"hive/{plan.session_id}/{agent.agent_id}"
        try:
            if not (await _git("git", "rev-parse", "--verify", branch, cwd=project)).strip():
                continue
        except RuntimeError:
            continue    # branch doesn't exist — nothing to salvage
        try:
            commits = int((await _git(
                "git", "rev-list", "--count", f"{main_branch}..{branch}",
                cwd=project)).strip() or "0")
        except RuntimeError:
            commits = 0
        if commits < 1:
            continue
        try:
            stat = await _git("git", "diff", "--shortstat",
                              f"{main_branch}...{branch}", cwd=project)
        except RuntimeError:
            stat = ""
        changed = sum(int(n) for n in re.findall(r"(\d+) (?:insertion|deletion)", stat))
        if changed < _SALVAGE_MIN_CHANGED_LINES:
            logger.info("Salvage skipped for %s — trivial branch (%d lines)",
                        agent.agent_id, changed)
            continue

        error = (result or {}).get("error") or "unknown failure"
        verdict = await _salvage_one(
            agent, branch, commits, error, session_id, project, main_branch, model)
        verdicts.append(verdict)
    return verdicts


async def _salvage_one(
    agent: SpawnedAgent, branch: str, commits: int, error: str,
    session_id: str, project: Path, main_branch: str, model: str,
) -> SalvageVerdict:
    from backend.workers.base import EventType, WorkerConfig
    from backend.workers.claude_cli import ClaudeCLIWorker
    from backend.worktrees.manager import WorktreeManager, _run as _git

    diff = ""
    try:
        diff = await _git("git", "diff", f"{main_branch}...{branch}", cwd=project)
    except RuntimeError:
        pass

    prompt = (
        f"You are the HIVE Reviewer performing a SALVAGE review. Agent "
        f"{agent.agent_id} ({agent.role}) FAILED: {error[:400]}\n\n"
        f"But its branch `{branch}` contains {commits} commit(s) of work. "
        f"Decide whether that work is worth keeping. Diff vs {main_branch}:\n"
        f"```diff\n{diff[:8000]}\n```\n\n"
        f"Reply with ONE JSON object, nothing else:\n"
        f'{{"action": "merge" | "discard", "reason": "one or two lines"}}\n'
        f"Choose merge if the work is correct/useful as-is (a later validation "
        f"pass still runs). Choose discard if it's broken, empty, or unsafe."
    )
    config = WorkerConfig(
        agent_id=f"salvage-{agent.agent_id}", session_id=session_id,
        model=model, worktree_path=str(project), max_turns=4,
    )
    action, reasoning = "discard", "salvage review produced no verdict"
    try:
        worker = ClaudeCLIWorker()
        chunks: list[str] = []
        final: str | None = None
        async for event in worker.run(prompt, config):
            if event.type == EventType.TEXT_DELTA and event.text:
                chunks.append(event.text)
            elif event.type == EventType.TEXT_DONE and event.text:
                final = event.text
            elif event.type == EventType.COST:
                try:
                    from backend.persistence.events import write_cost
                    await write_cost(session_id, config.agent_id,
                                     event.input_tokens or 0,
                                     event.output_tokens or 0,
                                     event.cost_usd or 0.0)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Salvage cost write failed: %s", exc)
            elif event.type == EventType.AGENT_ERROR:
                logger.warning("Salvage review errored: %s", event.error)
        raw = (final if final is not None else "".join(chunks)).strip()
        parsed = _first_json(raw)
        if parsed:
            action = "merge" if str(parsed.get("action")).lower() == "merge" else "discard"
            reasoning = str(parsed.get("reason") or "")[:400]
    except Exception as exc:  # noqa: BLE001 — a crash defaults to discard
        logger.warning("Salvage review crashed for %s: %s", agent.agent_id, exc)

    final_action = action
    if action == "merge":
        # Same merge path as a live agent — conflicts surface as conflicts.
        manager = WorktreeManager(session_id=session_id, project_path=str(project))
        merge = await manager.merge_to_main(agent_id=agent.agent_id, main_branch=main_branch)
        if not merge.success:
            final_action = "conflict"
            reasoning = f"salvage merge conflicted: {merge.conflict_files}; {reasoning}"

    try:
        from backend.persistence.events import write_event
        from backend.workers.base import HiveEvent
        await write_event(HiveEvent(
            type=EventType.SALVAGE_REVIEW, agent_id=agent.agent_id,
            session_id=session_id,
            raw_payload={"action": final_action, "reasoning": reasoning,
                         "commits": commits, "branch": branch,
                         "failure": error[:200]},
        ))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Salvage event write failed: %s", exc)

    logger.info("Salvage %s for %s: %s", final_action, agent.agent_id, reasoning[:80])
    return SalvageVerdict(agent_id=agent.agent_id, branch=branch,
                          action=final_action, reasoning=reasoning, commits=commits)


_SALVAGE_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _first_json(text: str) -> dict | None:
    match = _SALVAGE_JSON_RE.search(text or "")
    if not match:
        return None
    try:
        out = json.loads(match.group(0))
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None


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
