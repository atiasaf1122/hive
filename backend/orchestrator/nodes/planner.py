"""Planner node — analyzes the task and returns team composition.

Uses claude:sonnet during development (Opus reserved for production orchestration).
Returns a structured TeamComposition with roles, models, and counts.
"""
from __future__ import annotations

import json
import logging
import re

from backend.workers.base import EventType, WorkerConfig
from backend.workers.claude_cli import ClaudeCLIWorker

logger = logging.getLogger(__name__)

PLANNER_MODEL = "claude:sonnet"

_INSTRUCTIONS = """You are the Planner for HIVE, an AI agent orchestration system.
Analyze the given task and return ONLY a JSON object — no explanation, no markdown.

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
  "team": [
    {"role": "Builder", "model": "claude:sonnet", "count": 1, "passive": false}
  ],
  "confidence": 0.8,
  "rationale": "one line reason"
}

Rules: 1-5 active agents. passive=true for Debugger only. No markdown, no extra text."""


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


async def plan_team(
    task: str,
    session_id: str,
    model: str = PLANNER_MODEL,
) -> TeamComposition:
    """Call the Planner LLM and parse its team composition response."""
    worker = ClaudeCLIWorker()

    prompt = _INSTRUCTIONS + "\n\nTask: " + task + "\n\nJSON only:"

    config = WorkerConfig(
        agent_id=f"planner-{session_id}",
        session_id=session_id,
        model=model,
        worktree_path="/tmp",
        max_turns=3,
    )

    full_text: list[str] = []
    async for event in worker.run(prompt, config):
        if event.type == EventType.TEXT_DELTA and event.text:
            full_text.append(event.text)
        elif event.type == EventType.AGENT_ERROR:
            logger.error("Planner failed: %s", event.error)
            return _fallback_team()

    raw = "".join(full_text)
    return _parse_team_composition(raw)


def _parse_team_composition(raw: str) -> TeamComposition:
    """Extract the first JSON object from the LLM response."""
    data = _extract_first_json_object(raw)
    if data is None:
        logger.warning("Planner returned no JSON -- using fallback team")
        return _fallback_team()

    team = []
    for member in data.get("team", []):
        role = member.get("role", "Builder")
        model = member.get("model", "claude:sonnet")
        count = max(1, int(member.get("count", 1)))
        passive = bool(member.get("passive", False))
        team.append(TeamMember(role=role, model=model, count=count, passive=passive))

    if not team:
        return _fallback_team()

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
