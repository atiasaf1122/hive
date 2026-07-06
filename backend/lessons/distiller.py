"""Lesson distillation + trustworthiness gate (D1.2 / D1.3).

The anti-confabulation rules live here:
- distill() receives OBJECTIVE EVIDENCE ONLY (validator diagnoses, error
  payloads, llm_review reasoning) — never the agent's own narrative.
- "NONE" is a common, legitimate distillation outcome.
- Every draft passes a second groundedness check before it may be saved;
  a wrong lesson, once stored, poisons every future retrieval.

`LessonDistiller` is a small interface so the Haiku implementation can be
swapped for a local Ollama model later (2×RTX 3090 makes the whole loop
free at scale) without touching the write path.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

GATE_THRESHOLD = 8  # TAME trust-note pattern: score < 8 → discard


@dataclass
class LessonDraft:
    title: str
    description: str
    content: str
    trigger_context: str
    origin: str  # 'agent' | 'infrastructure'


@dataclass
class DistillResult:
    """E0.1 audit trail: when no draft is produced, `reason` says why —
    the model's own NONE explanation, or a parse-failure description.
    The write path persists it in the LESSON_NONE event so a stored-nothing
    outcome is never ambiguous again."""
    draft: LessonDraft | None
    reason: str = ""


@dataclass
class GateResult:
    score: int
    reason: str = ""


class LessonDistiller(Protocol):
    """Swap point: Haiku now, local Ollama later (E4)."""

    async def distill(self, evidence: str, *, origin: str) -> DistillResult: ...

    async def gate(self, draft: LessonDraft, evidence: str) -> GateResult: ...


_DISTILL_PROMPT = """You distill durable lessons for an AI coding swarm from OBJECTIVE EVIDENCE of a failure and (when present) its resolution.

Evidence (validator diagnoses, error payloads, review reasoning — nothing else exists):
---
{evidence}
---

Write a lesson ONLY if the evidence DIRECTLY supports it. If the root cause is
not determinable from the evidence alone, output: NONE: <one short line saying
what is missing from the evidence>
(NONE is a common, correct answer — never guess.)

If (and only if) the evidence supports a lesson, return ONE JSON object:
{{
  "title": "concise pitfall name (max 8 words)",
  "description": "one sentence naming the pitfall",
  "content": "1-3 sentences: the pitfall and how to avoid it, stated generally enough to reuse",
  "trigger_context": "what kind of task/situation this applies to (used for retrieval matching)"
}}
No markdown, no prose outside the JSON (or the single word NONE)."""

_GATE_PROMPT = """You are a strict fact-checker. Score how well EVERY claim in this drafted lesson is directly supported by the evidence.

Evidence:
---
{evidence}
---

Drafted lesson:
  title: {title}
  description: {description}
  content: {content}
  trigger_context: {trigger_context}

Score 0-10 where 10 = every claim is explicitly evidenced and 0 = invented.
Deduct heavily for any causal claim the evidence does not state outright.
Return ONE JSON object: {{"score": <0-10>, "reason": "one line"}} — nothing else."""


class HaikuLessonDistiller:
    """Production distiller over the budgeted Haiku caller."""

    def __init__(self, haiku_caller) -> None:
        self._call = haiku_caller

    async def distill(self, evidence: str, *, origin: str) -> DistillResult:
        raw = await self._call(_DISTILL_PROMPT.format(evidence=evidence[:6000]))
        text = (raw or "").strip()
        if not text:
            return DistillResult(None, reason="empty distiller response")
        if text.upper().startswith("NONE"):
            reason = text[4:].strip().lstrip(":—- ").strip() or "model returned NONE without elaboration"
            return DistillResult(None, reason=reason)
        data = _first_json(text)
        if not data:
            logger.warning("Distiller returned neither NONE nor JSON: %r", text[:120])
            return DistillResult(None, reason=f"unparseable distiller output: {text[:200]}")
        title = str(data.get("title") or "").strip()
        content = str(data.get("content") or "").strip()
        trigger = str(data.get("trigger_context") or "").strip()
        if not (title and content and trigger):
            return DistillResult(None, reason="draft missing required fields (title/content/trigger_context)")
        return DistillResult(LessonDraft(
            title=title[:120],
            description=str(data.get("description") or title).strip()[:300],
            content=content[:600],
            trigger_context=trigger[:400],
            origin=origin,
        ))

    async def gate(self, draft: LessonDraft, evidence: str) -> GateResult:
        raw = await self._call(_GATE_PROMPT.format(
            evidence=evidence[:6000], title=draft.title,
            description=draft.description, content=draft.content,
            trigger_context=draft.trigger_context,
        ))
        data = _first_json((raw or "").strip()) or {}
        reason = str(data.get("reason") or "").strip()[:300]
        try:
            return GateResult(max(0, min(10, int(data.get("score", 0)))), reason)
        except (TypeError, ValueError):
            return GateResult(0, reason or "gate returned no numeric score")


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _first_json(text: str) -> dict | None:
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        out = json.loads(match.group(0))
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        return None
