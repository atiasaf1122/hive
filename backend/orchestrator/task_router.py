"""Task-shape router (E3) — not everything deserves a swarm.

Before full planning, one cheap call classifies the request:

  SOLO  — single focused change, clear scope → ONE worker, no planner
          decomposition, no plan gate. Everything else stays (worktree,
          validation, summarizer, merge): a thinner pipe, not a different
          system.
  SWARM — multi-part / multi-file / needs decomposition → the full
          planner path, unchanged.
  CHAT  — question/discussion, no code change → answered in-session with
          a lightweight prompt; zero spawns.

The classifier dogfoods the hybrid pool: a local classification-capable
model when one is available (free), Haiku otherwise. Any failure →
SWARM: the full pipeline is the behavior HIVE always had, so the router
can only make things cheaper, never break them.

The decision (shape, reasoning, engine, override-or-not) is persisted as
a task/shape event so META and lessons can learn from misclassifications.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

VALID_SHAPES = ("solo", "swarm", "chat")
_SOLO_ROLES = ("Builder", "Writer", "Editor", "Researcher")

_RUBRIC = """Classify a user request for an AI coding-agent system. Reply with ONE JSON object, nothing else:
{{"shape": "solo" | "swarm" | "chat", "role": "Builder|Writer|Editor|Researcher", "mechanical": true/false, "reason": "one short line"}}

- "chat": a question, discussion, opinion, or explanation — NO file changes requested.
- "solo": ONE focused change with clear scope (one file/concern): fix a typo, rename X,
  add a null check, tweak a config, write one small file/doc.
- "swarm": multi-part or multi-file work, anything needing decomposition, design, or
  exploration.
- "role": for solo only — who should do it (code → Builder, prose/docs → Writer,
  small text edits → Editor, look-something-up → Researcher).
- "mechanical": for solo only — true when the change is fully specified by the request
  itself (no investigation of unfamiliar code needed).

Request:
---
{message}
---"""

_CHAT_INSTRUCTIONS = (
    "You are the orchestrator of HIVE, a multi-agent coding tool, chatting "
    "with its user. Answer the user directly and concisely. Do NOT propose "
    "an agent team; this turn was classified as conversation."
)


@dataclass
class ShapeDecision:
    shape: str                    # solo | swarm | chat
    reasoning: str
    engine: str                   # what classified: local:<model> | claude:haiku | override | fallback
    role: str = "Builder"
    mechanical: bool = False


async def resolve_task_shape(
    message: str,
    override: str = "auto",
) -> ShapeDecision:
    """Explicit user choice wins; otherwise classify; failure → swarm."""
    override = (override or "auto").lower()
    if override in VALID_SHAPES:
        return ShapeDecision(shape=override, reasoning="user override",
                             engine="override")
    try:
        return await _classify(message)
    except Exception as exc:  # noqa: BLE001 — router must never break a turn
        logger.warning("Task-shape classifier failed (%s) — defaulting to swarm", exc)
        return ShapeDecision(shape="swarm",
                             reasoning=f"classifier unavailable: {exc}",
                             engine="fallback")


async def _classify(message: str) -> ShapeDecision:
    prompt = _RUBRIC.format(message=message[:4000])

    local = await _local_classifier_model()
    if local is not None:
        raw = await _ollama_generate(prompt, local)
        decision = _parse(raw, engine=f"local:{local}")
        if decision is not None:
            return decision
        logger.info("Local classifier gave no usable JSON — trying Haiku")

    raw = await _haiku(prompt)
    decision = _parse(raw, engine="claude:haiku")
    if decision is not None:
        return decision
    raise ValueError("no usable JSON from any classifier")


async def _local_classifier_model() -> str | None:
    from backend.models_local import best_local_for, discover_local_models

    try:
        pool = await discover_local_models()
    except Exception:  # noqa: BLE001
        return None
    match = best_local_for("classification", pool)
    if match is None:
        return None
    # Prefer the SMALLEST capable model for classification — latency, and
    # it's usually already resident.
    small = [m for m in pool if m.available and "classification" in m.capabilities]
    return min(small, key=lambda m: m.size_gb).name if small else match.name


async def _ollama_generate(prompt: str, model: str, timeout_s: float = 30.0) -> str:
    from backend.detection import resolved_ollama_base

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(
            f"{resolved_ollama_base()}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        resp.raise_for_status()
        return str(resp.json().get("response") or "")


async def _haiku(prompt: str) -> str:
    from backend.llm.haiku import HaikuCaller
    from backend.workers.claude_cli import ClaudeCLIWorker

    caller = HaikuCaller(worker=ClaudeCLIWorker(), session_id="task-shape",
                         agent_id_prefix="shape-classifier")
    return await caller(prompt) or ""


_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _parse(raw: str, engine: str) -> ShapeDecision | None:
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    shape = str(data.get("shape") or "").strip().lower()
    if shape not in VALID_SHAPES:
        return None
    role = str(data.get("role") or "Builder").strip().title()
    if role not in _SOLO_ROLES:
        role = "Builder"
    return ShapeDecision(
        shape=shape,
        reasoning=str(data.get("reason") or "")[:300],
        engine=engine,
        role=role,
        mechanical=bool(data.get("mechanical", False)),
    )


# ── SOLO team synthesis ─────────────────────────────────────────────────────


async def build_solo_composition(message: str, decision: ShapeDecision):
    """One-agent team from the request itself — no planner call.

    Model choice follows the E2 routing guidance: a mechanical, fully
    specified change goes to the local coder when one is available
    (fallback haiku); anything else gets claude:sonnet.
    """
    from backend.models_local import best_local_for, discover_local_models
    from backend.orchestrator.nodes.planner import TeamComposition, TeamMember

    model = "claude:sonnet"
    if decision.mechanical:
        model = "claude:haiku"
        try:
            pool = await discover_local_models()
            coder = best_local_for("coding", pool)
            if coder is not None:
                model = f"ollama:{coder.name}"
        except Exception:  # noqa: BLE001
            pass

    member = TeamMember(
        role=decision.role, model=model, subtask=message.strip(),
        max_turns=12, fallback="haiku",
    )
    return TeamComposition(
        team=[member], confidence=0.9,
        rationale=f"solo route: {decision.reasoning}"[:200],
    )


# ── CHAT answer ─────────────────────────────────────────────────────────────


async def answer_chat(message: str, history: list[dict], session_id: str) -> str:
    """Lightweight in-session answer — no team schema, no plan machinery."""
    from backend.models import DEFAULT_MODEL
    from backend.workers.base import EventType, WorkerConfig
    from backend.workers.claude_cli import ClaudeCLIWorker

    parts = [_CHAT_INSTRUCTIONS, ""]
    for entry in (history or [])[-6:]:
        content = (entry.get("content") or "").strip()
        if content:
            parts.append(f"{entry.get('role', 'user').capitalize()}: {content}")
    parts.append(f"User: {message}")
    prompt = "\n".join(parts)

    config = WorkerConfig(
        agent_id=f"chat-{session_id}", session_id=session_id,
        # E0.5 finding: with the CLI's full default toolset the chat call
        # kept reaching for tools and died on the 2-turn cap (every CHAT
        # turn then fell back to the full planner — resilient but never
        # thin). Restrict to Read and give a little headroom.
        model=DEFAULT_MODEL, worktree_path="/tmp", max_turns=4,
        allowed_tools=["Read"],
    )
    worker = ClaudeCLIWorker()
    chunks: list[str] = []
    final: str | None = None
    async for event in worker.run(prompt, config):
        if event.type == EventType.TEXT_DELTA and event.text:
            chunks.append(event.text)
        elif event.type == EventType.TEXT_DONE and event.text:
            final = event.text
        elif event.type == EventType.AGENT_ERROR:
            raise RuntimeError(event.error or "chat answer failed")
    return (final if final is not None else "".join(chunks)).strip()
