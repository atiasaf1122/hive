"""Orchestrator context compaction (D3) — reconstruct state, don't accumulate.

Long multi-turn sessions bloat the orchestrator's conversation history.
At a token threshold (or every N turns) one Haiku call rewrites the
history into a compact CURRENT STATE document; subsequent turns are built
from system prompt + state doc + the last K raw turns. The pruned turns
are persisted in the compaction event, so nothing is lost.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# ~70% of a comfortable planner budget; env-tunable without redeploys.
COMPACT_AT_TOKENS = int(os.environ.get("HIVE_COMPACT_AT_TOKENS", "20000"))
COMPACT_EVERY_TURNS = int(os.environ.get("HIVE_COMPACT_EVERY_TURNS", "12"))
KEEP_LAST_TURNS = 4

_PROMPT = """You maintain the CURRENT STATE document for a long-running engineering session.

Original goal:
{task}

Previous state document (may be empty):
---
{prev_doc}
---

Conversation turns being compacted away (chronological):
---
{history}
---

Rewrite the state document. Be compact and factual — this replaces the raw
turns, so anything not captured here is invisible to future planning.
Sections (omit empty ones):
# Goal
# Decisions made
# Done
# Open / in progress
# Active constraints
# Key files
Return ONLY the document text, no preamble."""


def estimate_tokens(history: list[dict]) -> int:
    """Cheap char/4 approximation — we need a trigger, not an exact count."""
    return len(json.dumps(history, ensure_ascii=False)) // 4


def should_compact(history: list[dict], turns_since_compaction: int) -> bool:
    if turns_since_compaction >= COMPACT_EVERY_TURNS:
        return True
    return estimate_tokens(history) >= COMPACT_AT_TOKENS


async def build_state_doc(
    history: list[dict],
    prev_doc: str,
    task: str,
    haiku_caller=None,
    session_id: str = "",
) -> str | None:
    """One Haiku call → new state doc. Returns None on failure (callers
    skip compaction rather than lose context)."""
    try:
        if haiku_caller is None:
            from backend.llm.haiku import HaikuCaller
            from backend.workers.claude_cli import ClaudeCLIWorker
            haiku_caller = HaikuCaller(
                worker=ClaudeCLIWorker(), session_id=session_id or "compaction",
                agent_id_prefix="compactor",
            )
        rendered = "\n".join(
            f"[{m.get('role')}] {(m.get('content') or '')[:1500]}" for m in history
        )
        doc = await haiku_caller(_PROMPT.format(
            task=task[:1500], prev_doc=(prev_doc or "(none)")[:4000],
            history=rendered[:24000],
        ))
        doc = (doc or "").strip()
        return doc or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Compaction failed — keeping full history: %s", exc)
        return None
