"""Orchestrator decision-maker.

The orchestrator is the user's permanent contact for a project. Every user
message is fed to `orchestrate()`, which decides what to do:

  - Just answer (chat, question, follow-up)        → response, empty team
  - Spawn agents to build something                → response + team
  - Both (announce the plan + spawn)               → response + team

Backwards-compat:
  - `_parse_team_composition` and `plan_team` are still exported so existing
    parser-only tests keep working.
  - `OrchestratorDecision` is a thin wrapper over `TeamComposition` with an
    extra `response` string.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from backend.workers.base import EventType, WorkerConfig
from backend.workers.claude_cli import ClaudeCLIWorker

logger = logging.getLogger(__name__)

PLANNER_MODEL = "claude:sonnet"

_INSTRUCTIONS = """You are the Orchestrator for HIVE, an AI agent swarm.
The user is your permanent contact for this project — they may send many messages over its lifetime.
Look at the conversation history and the latest user message, then decide what to do.

Return ONLY a JSON object — no explanation, no markdown.

Available roles:
- Thinker: architecture planning. model: claude:sonnet
- Builder: writing code. model: claude:sonnet (can spawn multiple)
- Tester: writing and running tests. model: claude:sonnet
- Debugger: fixing failures (passive). model: claude:sonnet
- Researcher: gathering information. model: claude:sonnet
- Writer: creating written content. model: claude:sonnet
- Editor: editing/proofreading. model: claude:sonnet
- Translator: translation. model: claude:sonnet
- DocReader: reading long documents. model: claude:sonnet

Return this exact JSON structure:
{
  "response": "your direct reply to the user (always present, can be brief)",
  "team": [
    {"role": "Builder", "model": "claude:sonnet", "count": 1, "passive": false}
  ],
  "confidence": 0.8,
  "rationale": "one line reason"
}

Rules:
- If the user is just chatting / asking a question / following up → set `team: []` and put your answer in `response`.
- If the user wants something built/edited/fixed → list 1-5 active agents in `team` and put a short acknowledgement in `response`.
- passive=true for Debugger only.
- No markdown, no extra text outside the JSON."""


class TeamMember:
    def __init__(self, role: str, model: str, count: int, passive: bool = False) -> None:
        self.role = role
        self.model = model
        self.count = count
        self.passive = passive

    def __repr__(self) -> str:
        return f"TeamMember(role={self.role}, model={self.model}, count={self.count}, passive={self.passive})"


class TeamComposition:
    def __init__(self, team: list[TeamMember], confidence: float, rationale: str) -> None:
        self.team = team
        self.confidence = confidence
        self.rationale = rationale

    @property
    def total_active(self) -> int:
        return sum(m.count for m in self.team if not m.passive)

    def __repr__(self) -> str:
        return f"TeamComposition(members={self.team}, confidence={self.confidence:.2f})"


class OrchestratorDecision:
    """A single orchestrator turn output: a reply to the user, plus an optional team to spawn."""

    def __init__(self, response: str, composition: TeamComposition) -> None:
        self.response = response
        self.composition = composition

    @property
    def has_active_team(self) -> bool:
        return self.composition.total_active > 0

    def __repr__(self) -> str:
        return f"OrchestratorDecision(response={self.response[:40]!r}, team={self.composition})"


async def orchestrate(
    message: str,
    session_id: str,
    history: list[dict] | None = None,
    model: str = PLANNER_MODEL,
    project_path: str | None = None,
) -> OrchestratorDecision:
    """Run one orchestrator turn — answer the user, optionally with a team to spawn.

    `project_path` is currently NOT used as the planner's cwd. Reason:
    a planner spawned at the workspace cwd is a `claude --dangerously-
    skip-permissions` process with write access to the project. In
    testing it ignored the JSON-only instruction and just *built the
    project itself* — leaving untracked files in master that then
    broke the Reviewer's merge of the builder's worktree.
    Workers do the editing; the planner plans. Keep this `/tmp`
    until we wire `--allowed-tools` to restrict the planner to
    Read/Grep/Glob.
    """
    worker = ClaudeCLIWorker()
    prompt = _build_prompt(message, history or [])

    # Project_path is accepted for future read-only modes but currently
    # ignored — see the docstring above.
    _ = project_path
    cwd = "/tmp"

    config = WorkerConfig(
        agent_id=f"orchestrator-{session_id}",
        session_id=session_id,
        model=model,
        worktree_path=cwd,
        max_turns=3,
    )

    chunks: list[str] = []
    final_text: str | None = None  # populated when TEXT_DONE arrives
    async for event in worker.run(prompt, config):
        if event.type == EventType.TEXT_DELTA and event.text:
            chunks.append(event.text)
        elif event.type == EventType.TEXT_DONE and event.text:
            # The consolidated assistant message — supersedes partial
            # chunks so we don't end up double-counting the text.
            final_text = event.text
        elif event.type == EventType.AGENT_ERROR:
            logger.error("Orchestrator failed: %s", event.error)
            return OrchestratorDecision(
                response="(orchestrator failed — please try again)",
                composition=_fallback_team(),
            )

    return _parse_decision(final_text if final_text is not None else "".join(chunks))


def _build_prompt(message: str, history: list[dict]) -> str:
    parts = [_INSTRUCTIONS, ""]
    if history:
        parts.append("Conversation so far:")
        for entry in history[-10:]:  # last 10 turns
            role = entry.get("role", "user").capitalize()
            content = (entry.get("content") or "").strip()
            if content:
                parts.append(f"{role}: {content}")
        parts.append("")
    parts.append(f"User: {message}")
    parts.append("")
    parts.append("JSON only:")
    return "\n".join(parts)


def _parse_decision(raw: str) -> OrchestratorDecision:
    data = _extract_first_json_object(raw)
    if data is None:
        logger.warning("Orchestrator returned no JSON -- defaulting to chat-only response")
        return OrchestratorDecision(
            response=raw.strip() or "(no response)",
            composition=TeamComposition(team=[], confidence=0.5, rationale="no JSON returned"),
        )

    response = (data.get("response") or "").strip()
    composition = _parse_composition_dict(data)
    return OrchestratorDecision(response=response, composition=composition)


async def plan_team(
    task: str,
    session_id: str,
    model: str = PLANNER_MODEL,
) -> TeamComposition:
    """Backwards-compat shim: returns only the team part of an orchestrator turn."""
    decision = await orchestrate(message=task, session_id=session_id, model=model)
    return decision.composition


def _parse_team_composition(raw: str) -> TeamComposition:
    """Extract the first JSON object from the LLM response.

    Guarantees the returned TeamComposition has at least one active (non-passive)
    agent when one was clearly intended — coding tasks must never reach spawn
    with an empty team.
    """
    data = _extract_first_json_object(raw)
    if data is None:
        logger.warning("Planner returned no JSON -- using fallback team")
        return _fallback_team()

    composition = _parse_composition_dict(data)

    if composition.total_active == 0:
        logger.warning("Planner returned 0 active agents -- inserting default Builder")
        composition.team.append(
            TeamMember(role="Builder", model="claude:sonnet", count=1, passive=False)
        )
        if not composition.rationale:
            composition.rationale = "auto-added default Builder (planner returned no active agents)"

    return composition


def _parse_composition_dict(data: dict) -> TeamComposition:
    """Same parsing path as `_parse_team_composition` but without the auto-floor.

    The orchestrator path needs an empty team to be a legitimate signal that
    "no agents needed, just answer". So we keep raw zero-active teams intact.
    """
    team = []
    for member in data.get("team", []):
        role = member.get("role", "Builder")
        model = member.get("model", "claude:sonnet")
        count = max(1, int(member.get("count", 1)))
        passive = bool(member.get("passive", False))
        team.append(TeamMember(role=role, model=model, count=count, passive=passive))

    return TeamComposition(
        team=team,
        confidence=float(data.get("confidence", 0.7)),
        rationale=data.get("rationale", ""),
    )


def _extract_first_json_object(text: str) -> dict | None:
    """Find the first complete JSON object by tracking brace depth."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth == 0:
            try:
                return json.loads(text[start : i + 1])
            except json.JSONDecodeError as exc:
                logger.warning("Planner JSON parse error: %s", exc)
                return None
    return None


def _fallback_team() -> TeamComposition:
    """Minimal single-builder team used when planning fails."""
    return TeamComposition(
        team=[TeamMember(role="Builder", model="claude:sonnet", count=1)],
        confidence=0.5,
        rationale="fallback: planning failed, using single Builder",
    )
