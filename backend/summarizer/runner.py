"""Tiered summarisation backed by a Haiku one-shot call.

The runner is intentionally LLM-agnostic — it consumes an injected
`haiku_caller: Callable[[str], Awaitable[str]]`. In production that
caller is a `HaikuCaller` (`backend.llm.haiku`); tests pass a
deterministic stub.

Three tiers are produced from the same Haiku response. We ask Haiku
for a single JSON object (no chain-of-three calls) and split it into
TL;DR / standard / detailed locally — that keeps the budget impact to
roughly one Haiku call per worker turn.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Awaitable, Callable

from backend.validation.schema import (
    CompletionReport,
    Evidence,
    FileTouched,
    TestRun,
)
from backend.workers.base import EventType, HiveEvent

logger = logging.getLogger(__name__)


class SummaryTier(StrEnum):
    TLDR = "tldr"
    STANDARD = "standard"
    DETAILED = "detailed"


class SummarizerError(RuntimeError):
    """Raised when Haiku returned no JSON we can parse."""


@dataclass
class TieredSummary:
    """The three views the UI consumes."""
    tldr: str = ""
    standard: str = ""
    detailed: CompletionReport | None = None
    # The raw JSON object Haiku returned, useful for debugging.
    raw: dict[str, Any] = field(default_factory=dict)

    def for_tier(self, tier: SummaryTier) -> Any:
        if tier == SummaryTier.TLDR:
            return self.tldr
        if tier == SummaryTier.STANDARD:
            return self.standard
        return self.detailed


# ─── Public API ─────────────────────────────────────────────────────────────


async def summarize_events(
    events: Iterable[HiveEvent | dict[str, Any]],
    *,
    haiku_caller: Callable[[str], Awaitable[str]],
    task_description: str = "",
    max_transcript_chars: int = 12_000,
) -> TieredSummary:
    """Collapse an event stream into a tiered summary.

    `events` may be HiveEvent objects (as the worker emits) or plain
    dicts (when replayed from SQLite). We extract the worker's text +
    tool activity + cost, render a compact transcript, and ask Haiku
    for one structured JSON response.
    """
    transcript = _events_to_transcript(events, max_chars=max_transcript_chars)
    return await summarize_transcript(
        transcript,
        haiku_caller=haiku_caller,
        task_description=task_description,
    )


async def summarize_transcript(
    transcript: str,
    *,
    haiku_caller: Callable[[str], Awaitable[str]],
    task_description: str = "",
) -> TieredSummary:
    """Same as `summarize_events`, but takes a pre-rendered transcript.

    Useful when the caller wants to inject additional context
    (e.g. concatenating multiple agents' transcripts before
    summarising for a session-level rollup).
    """
    prompt = _build_prompt(transcript, task_description)
    raw = await haiku_caller(prompt)
    return _parse_response(raw)


# ─── Internals ──────────────────────────────────────────────────────────────


def _events_to_transcript(
    events: Iterable[HiveEvent | dict[str, Any]],
    *,
    max_chars: int,
) -> str:
    """Render the events as a compact chronological log."""
    parts: list[str] = []
    for ev in events:
        d = ev.model_dump() if isinstance(ev, HiveEvent) else ev
        etype = str(d.get("type", "")).split("/")[0]  # 'text/delta' → 'text'
        kind = str(d.get("type", ""))
        if kind in (EventType.TEXT_DELTA, "text/delta", EventType.TEXT_DONE, "text/done"):
            text = (d.get("text") or "").strip()
            if text:
                parts.append(text)
        elif kind == EventType.TOOL_USE or kind == "tool/use":
            tname = d.get("tool_name") or "<tool>"
            tinp = d.get("tool_input") or {}
            try:
                inp_str = json.dumps(tinp, ensure_ascii=False)[:200]
            except Exception:  # noqa: BLE001
                inp_str = str(tinp)[:200]
            parts.append(f"[tool {tname}] {inp_str}")
        elif kind == EventType.TOOL_RESULT or kind == "tool/result":
            result = d.get("tool_result")
            if isinstance(result, list):
                # Anthropic content blocks
                snippet = " ".join(
                    (b.get("text") or "") for b in result if isinstance(b, dict)
                )
            else:
                snippet = str(result or "")
            snippet = snippet.strip().replace("\n", " ")[:200]
            parts.append(f"[result] {snippet}")
        elif kind == EventType.AGENT_ERROR or kind == "agent/error":
            parts.append(f"[error] {d.get('error') or ''}")
        # COST + RATE_LIMIT + RAW skipped — they add noise.

    transcript = "\n".join(parts)
    if len(transcript) > max_chars:
        head = transcript[: max_chars // 2]
        tail = transcript[-max_chars // 2:]
        transcript = f"{head}\n\n…[trimmed {len(transcript) - max_chars} chars]…\n\n{tail}"
    return transcript


def _build_prompt(transcript: str, task_description: str) -> str:
    task = task_description.strip() or "(no task description provided)"
    return (
        "You are the Summarizer for an autonomous coding agent. Read the agent's "
        "raw event log and produce a SINGLE JSON object — no prose around it.\n\n"
        f"Task the agent was given:\n  {task}\n\n"
        "Agent transcript (chronological):\n"
        f"---\n{transcript}\n---\n\n"
        "Return exactly this JSON shape:\n"
        "{\n"
        '  "tldr": "one short sentence the user reads in chat",\n'
        '  "standard": "a paragraph (max 4 sentences) summarising what happened",\n'
        '  "status": "done|failed|blocked|needs_approval",\n'
        '  "description": "the standard summary, restated",\n'
        '  "key_decisions": ["…", "…"],\n'
        '  "open_questions": ["…"],\n'
        '  "technical_debt": ["…"],\n'
        '  "follow_up_tasks_recommended": ["…"],\n'
        '  "evidence": {\n'
        '    "git_commits": ["sha…"],\n'
        '    "files_touched": [{"path": "…", "action": "created|modified|deleted",\n'
        '                       "lines_added": 0, "lines_removed": 0,\n'
        '                       "what_was_done": "…"}],\n'
        '    "tests_run": [{"command": "…", "exit_code": 0, "excerpt": "…"}],\n'
        '    "packages_installed": ["…"],\n'
        '    "diff_summary": "…",\n'
        '    "commands_run": ["…"]\n'
        "  }\n"
        "}\n\n"
        "Use only what's actually in the transcript — don't invent commits, files, "
        "or tests. If a field has no evidence, return an empty array or string."
    )


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_response(raw: str) -> TieredSummary:
    text = (raw or "").strip()
    if not text:
        raise SummarizerError("Haiku returned an empty response.")

    # Strip ```json fences if Haiku added them.
    if text.startswith("```"):
        text = text.strip("`")
        # remove a leading language tag like "json"
        first_nl = text.find("\n")
        if first_nl >= 0:
            text = text[first_nl + 1:]
        text = text.strip("` \n")

    match = _JSON_RE.search(text)
    if not match:
        raise SummarizerError(f"No JSON object in Haiku response: {raw[:200]!r}")

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise SummarizerError(f"Haiku JSON failed to parse: {exc}") from exc

    detailed = _build_detailed(data)
    return TieredSummary(
        tldr=str(data.get("tldr", "")).strip(),
        standard=str(data.get("standard", "")).strip(),
        detailed=detailed,
        raw=data,
    )


def _build_detailed(data: dict[str, Any]) -> CompletionReport:
    evidence_raw = data.get("evidence") or {}
    files = []
    for f in evidence_raw.get("files_touched", []) or []:
        if not isinstance(f, dict) or not f.get("path"):
            continue
        action = f.get("action", "modified")
        if action not in ("created", "modified", "deleted"):
            action = "modified"
        files.append(FileTouched(
            path=f["path"], action=action,
            lines_added=int(f.get("lines_added") or 0),
            lines_removed=int(f.get("lines_removed") or 0),
            what_was_done=str(f.get("what_was_done") or "").strip(),
        ))
    tests = []
    for t in evidence_raw.get("tests_run", []) or []:
        if not isinstance(t, dict) or not t.get("command"):
            continue
        tests.append(TestRun(
            command=t["command"],
            exit_code=int(t.get("exit_code") or 0),
            excerpt=str(t.get("excerpt") or "")[:500],
        ))

    return CompletionReport(
        status=_safe_status(data.get("status")),
        description=str(data.get("description") or data.get("standard") or "").strip(),
        key_decisions=_clean_list(data.get("key_decisions")),
        open_questions=_clean_list(data.get("open_questions")),
        technical_debt=_clean_list(data.get("technical_debt")),
        follow_up_tasks_recommended=_clean_list(
            data.get("follow_up_tasks_recommended"),
        ),
        evidence=Evidence(
            git_commits=_clean_list(evidence_raw.get("git_commits")),
            files_touched=files,
            tests_run=tests,
            packages_installed=_clean_list(evidence_raw.get("packages_installed")),
            diff_summary=str(evidence_raw.get("diff_summary") or "").strip(),
            commands_run=_clean_list(evidence_raw.get("commands_run")),
        ),
    )


def _safe_status(value: Any) -> str:
    text = str(value or "done").strip().lower()
    if text in ("done", "failed", "blocked", "needs_approval"):
        return text
    return "done"


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
