"""Plan-quality gate (D2) — stop bad plans before they burn dollars.

One budgeted Haiku call scores the emitted plan against a fixed rubric
(coverage / overlap / fit / size). Score >= 7 proceeds silently; below
that, the issues surface in the approval modal after at most ONE automatic
revision round — the user always decides. The gate FAILS OPEN: an outage
must never block work.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

PASS_SCORE = 7

_RUBRIC = """You are a plan reviewer for an AI agent swarm. Score this team plan against the user's request.

User request:
---
{request}
---

Plan (one entry per agent):
{plan}

Rubric — deduct for each concrete problem:
- Coverage: do the subtasks JOINTLY cover the request? Anything missing or extra?
- Overlap: do any two agents' files_hint sets or subtask descriptions collide?
- Fit: are model tiers sane for subtask difficulty? Is MCP equipment matched to need
  (browser tools only where a real browser is needed, never on haiku)?
- Size: is the agent count justified, or would fewer (or a single agent) do?

Return ONE JSON object, nothing else:
{{"score": <0-10>, "issues": ["one line per concrete problem, empty if none"]}}"""


@dataclass
class PlanScore:
    score: int
    issues: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.score >= PASS_SCORE

    def to_dict(self) -> dict:
        return {"score": self.score, "issues": self.issues, "passed": self.passed}


def _render_plan(composition: dict) -> str:
    lines = []
    for m in composition.get("team", []):
        lines.append(
            f"- {m.get('role')} [{m.get('model')}] max_turns={m.get('max_turns')} "
            f"files={m.get('files_hint')} mcp={m.get('mcp_servers')}\n"
            f"  subtask: {m.get('subtask')}"
        )
    return "\n".join(lines) or "(empty)"


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


async def score_plan(
    composition: dict, user_request: str, haiku_caller=None, session_id: str = ""
) -> PlanScore:
    """Score a plan. Fails OPEN (score 10) on any error."""
    try:
        if haiku_caller is None:
            from backend.llm.haiku import HaikuCaller
            from backend.workers.claude_cli import ClaudeCLIWorker
            haiku_caller = HaikuCaller(
                worker=ClaudeCLIWorker(), session_id=session_id or "plan-gate",
                agent_id_prefix="plan-gate",
            )
        raw = await haiku_caller(_RUBRIC.format(
            request=user_request[:3000], plan=_render_plan(composition)[:4000]))
        match = _JSON_RE.search((raw or "").strip())
        data = json.loads(match.group(0)) if match else {}
        score = max(0, min(10, int(data.get("score", 10))))
        issues = [str(i) for i in (data.get("issues") or []) if str(i).strip()]
        return PlanScore(score=score, issues=issues)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Plan gate failed open: %s", exc)
        return PlanScore(score=10, issues=[])
