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
{{"shape": "solo" | "swarm" | "chat", "role": "Builder|Writer|Editor|Researcher", "mechanical": true/false, "needs_tools": true/false, "reason": "one short line"}}

- "chat": a question, discussion, opinion, or explanation — NO file changes requested.
- "solo": ONE focused change with clear scope (one file/concern): fix a typo, rename X,
  add a null check, tweak a config, write one small file/doc.
- "swarm": multi-part or multi-file work, anything needing decomposition, design, or
  exploration.
- "role": for solo only — who should do it (code → Builder, prose/docs → Writer,
  small text edits → Editor, look-something-up → Researcher).
- "mechanical": for solo only — true when the change is fully specified by the request
  itself (no investigation of unfamiliar code needed).
- "needs_tools": true when the task requires interaction BEYOND plain file editing —
  driving a real browser (Playwright/Puppeteer/Selenium), taking a screenshot, running an
  end-to-end/e2e check against a live app, web search/fetch, or any other MCP tool. false
  when writing/editing/reading files (even many of them) is enough. When unsure, answer
  true — under-equipping a tool task fails silently.

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
    # G1: does the task need browser/MCP/tool interaction beyond file edits?
    # A first-class judgment — needs_tools=True never routes to a local
    # worker (local has no tool loop). True is the safe default.
    needs_tools: bool = False


async def resolve_task_shape(
    message: str,
    override: str = "auto",
    session_id: str = "",
) -> ShapeDecision:
    """Explicit user choice wins; otherwise classify; failure → swarm."""
    override = (override or "auto").lower()
    if override == "chat":
        # No attributes needed to answer in-session.
        return ShapeDecision(shape="chat", reasoning="user override",
                             engine="override")
    if override in VALID_SHAPES:
        # F0.4 finding: the override used to skip classification entirely,
        # so `mechanical` stayed False and an overridden Solo could never
        # route to a local model. The override forces the SHAPE; the
        # classifier still supplies role/mechanical for model choice.
        try:
            classified = await _classify(message, session_id)
            decision = ShapeDecision(shape=override,
                                     reasoning=f"user override ({classified.reasoning})",
                                     engine="override",
                                     role=classified.role,
                                     mechanical=classified.mechanical,
                                     needs_tools=classified.needs_tools)
            return await _apply_tools_backstop(decision, message, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Classifier failed under override (%s) — defaults", exc)
            return ShapeDecision(shape=override, reasoning="user override",
                                 engine="override")
    try:
        decision = await _classify(message, session_id)
    except Exception as exc:  # noqa: BLE001 — router must never break a turn
        logger.warning("Task-shape classifier failed (%s) — defaulting to swarm", exc)
        return ShapeDecision(shape="swarm",
                             reasoning=f"classifier unavailable: {exc}",
                             engine="fallback")
    return await _apply_tools_backstop(decision, message, session_id)


async def _apply_tools_backstop(
    decision: ShapeDecision, message: str, session_id: str,
) -> ShapeDecision:
    """G1: the F5 keyword scan is now only a VALIDATION backstop on the
    classifier's needs_tools judgment. If keywords scream tools but the 8B
    said needs_tools=false, don't override silently — log a
    CLASSIFIER_DISAGREEMENT event (we want to measure the 8B's judgment) and
    route by the SAFER verdict (tools=true → Claude)."""
    keyword_tools = bool(_TOOL_RELIANT_RE.search(message))
    if keyword_tools and not decision.needs_tools:
        logger.info("Classifier said needs_tools=false but keywords disagree — "
                    "routing safe (tools)")
        if session_id:
            try:
                from backend.persistence.events import write_event
                from backend.workers.base import EventType, HiveEvent
                await write_event(HiveEvent(
                    type=EventType.CLASSIFIER_DISAGREEMENT,
                    agent_id="task-shape", session_id=session_id,
                    raw_payload={"classifier_needs_tools": False,
                                 "keyword_needs_tools": True,
                                 "shape": decision.shape,
                                 "reasoning": decision.reasoning}))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Disagreement event write failed: %s", exc)
        decision.needs_tools = True     # route by the safer verdict
    return decision


async def _classify(message: str, session_id: str = "") -> ShapeDecision:
    prompt = _RUBRIC.format(message=message[:4000])

    local = await _local_classifier_model()
    if local is not None:
        raw = await _ollama_generate(prompt, local, session_id=session_id)
        decision = _parse(raw, engine=f"local:{local}")
        if decision is not None:
            return decision
        logger.info("Local classifier gave no usable JSON — trying Haiku")

    raw = await _haiku(prompt, session_id)
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


async def _ollama_generate(prompt: str, model: str, timeout_s: float = 30.0,
                           session_id: str = "") -> str:
    from backend.detection import resolved_ollama_base

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(
            f"{resolved_ollama_base()}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
        )
        resp.raise_for_status()
        data = resp.json()
        if session_id:
            # F0.1: token counts at $0 — keep classifier context data.
            try:
                from backend.persistence.events import write_cost
                await write_cost(session_id, f"shape-classifier-{session_id}",
                                 int(data.get("prompt_eval_count") or 0),
                                 int(data.get("eval_count") or 0), 0.0,
                                 local=True)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Classifier cost write failed: %s", exc)
        return str(data.get("response") or "")


async def _haiku(prompt: str, session_id: str = "") -> str:
    from backend.llm.haiku import HaikuCaller
    from backend.workers.claude_cli import ClaudeCLIWorker

    # F0.1: a real session_id makes the cost row land (the old hardcoded
    # "task-shape" violated the sessions FK and the write silently failed).
    caller = HaikuCaller(worker=ClaudeCLIWorker(),
                         session_id=session_id or "task-shape",
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
        needs_tools=bool(data.get("needs_tools", False)),
    )


# ── SOLO team synthesis ─────────────────────────────────────────────────────


# F5 finding: a SOLO task that needs a real browser / tool-use loop must
# NOT route to a local worker — local workers have no tool loop (file-block
# harness only), so browser verification silently produces nothing. This
# enforces the E3/E2 rule "MCP-equipped subtasks stay on Claude tiers" for
# the solo path, which build_solo_composition previously skipped.
_TOOL_RELIANT_RE = re.compile(
    r"\b(playwright|puppeteer|selenium|browser|screenshot|e2e|"
    r"end.to.end|headless|chromium|webdriver|navigate to|click the)\b",
    re.IGNORECASE)


# Browser-shaped tasks get the playwright MCP; other tool needs
# (web search etc.) are handled by the CLI's built-in tools on a Claude
# tier — only browser drive needs a server attached.
_BROWSER_RE = re.compile(
    r"\b(playwright|puppeteer|selenium|browser|screenshot|headless|"
    r"chromium|webdriver|navigate to|click the)\b", re.IGNORECASE)


async def build_solo_composition(message: str, decision: ShapeDecision):
    """One-agent team from the request itself — no planner call.

    G1 routing: needs_tools (a first-class classifier field, backstopped by
    the keyword scan) → never a local worker; browser-shaped → attach the
    playwright MCP. Otherwise a mechanical, fully-specified change goes to
    the local coder when available (fallback haiku); anything else sonnet.
    """
    from backend.models_local import best_local_for, discover_local_models
    from backend.orchestrator.nodes.planner import TeamComposition, TeamMember

    needs_tools = decision.needs_tools
    model = "claude:sonnet"
    if decision.mechanical and not needs_tools:
        model = "claude:haiku"
        try:
            pool = await discover_local_models()
            coder = best_local_for("coding", pool)
            if coder is not None:
                model = f"ollama:{coder.name}"
        except Exception:  # noqa: BLE001
            pass

    mcp_servers: list[str] = []
    if needs_tools and _BROWSER_RE.search(message):
        mcp_servers = ["playwright"]

    # A tool-reliant solo burns turns fast (install + drive + verify +
    # screenshot) — 12 starved the palette solo (E0.3 MCP-turn lesson).
    max_turns = 28 if needs_tools else 12
    member = TeamMember(
        role=decision.role, model=model, subtask=message.strip(),
        max_turns=max_turns, fallback="haiku", mcp_servers=mcp_servers,
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
        elif event.type == EventType.COST:
            try:
                from backend.persistence.events import write_cost
                await write_cost(session_id, config.agent_id,
                                 event.input_tokens or 0,
                                 event.output_tokens or 0,
                                 event.cost_usd or 0.0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Chat cost write failed: %s", exc)
        elif event.type == EventType.AGENT_ERROR:
            raise RuntimeError(event.error or "chat answer failed")
    return (final if final is not None else "".join(chunks)).strip()
