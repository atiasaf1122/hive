"""Tests for the Summarizer (Item 3 — Tiered reporting)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from backend.summarizer.runner import (
    SummarizerError,
    SummaryTier,
    summarize_events,
    summarize_transcript,
)
from backend.validation.schema import CompletionReport
from backend.workers.base import EventType, HiveEvent


# ── helpers ──────────────────────────────────────────────────────────────────


def _event(kind: EventType, **kw: Any) -> HiveEvent:
    return HiveEvent(
        type=kind, agent_id="agent-sum", session_id="sess-sum", **kw,
    )


def _make_haiku(response: str):
    """Return an async callable that records the prompt and replies with `response`."""
    seen: dict[str, str] = {}

    async def caller(prompt: str) -> str:
        seen["prompt"] = prompt
        return response

    caller.seen = seen  # type: ignore[attr-defined]
    return caller


_OK_RESPONSE = json.dumps({
    "tldr": "Added a login form.",
    "standard": "The agent created src/Login.tsx with email + password fields and ran the existing test suite. All tests pass.",
    "status": "done",
    "description": "Login form scaffolded and tests green.",
    "key_decisions": ["Used React 18 Hook Form", "Skipped reset-password flow"],
    "open_questions": [],
    "technical_debt": ["No e2e Playwright spec yet"],
    "follow_up_tasks_recommended": ["Add Playwright login spec"],
    "evidence": {
        "git_commits": ["abc1234"],
        "files_touched": [{
            "path": "src/Login.tsx", "action": "created",
            "lines_added": 42, "lines_removed": 0,
            "what_was_done": "Wrote the controlled form + submit handler.",
        }],
        "tests_run": [{
            "command": "npm test", "exit_code": 0, "excerpt": "12 passed",
        }],
        "packages_installed": ["react-hook-form"],
        "diff_summary": "1 file changed, 42 insertions(+)",
        "commands_run": ["git add src/Login.tsx", "npm test"],
    },
})


# ── core parsing ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summarize_transcript_returns_three_tiers():
    haiku = _make_haiku(_OK_RESPONSE)
    summary = await summarize_transcript(
        "[tool Edit] {...}\n[result] wrote file\n",
        haiku_caller=haiku,
        task_description="Add a login form",
    )
    assert summary.tldr == "Added a login form."
    assert "controlled form" in summary.standard or "tests pass" in summary.standard
    assert isinstance(summary.detailed, CompletionReport)
    assert summary.detailed.status == "done"
    assert summary.detailed.evidence.files_touched[0].path == "src/Login.tsx"
    assert summary.detailed.evidence.tests_run[0].exit_code == 0

    # The for_tier accessor mirrors the underlying fields.
    assert summary.for_tier(SummaryTier.TLDR) == summary.tldr
    assert summary.for_tier(SummaryTier.STANDARD) == summary.standard
    assert summary.for_tier(SummaryTier.DETAILED) is summary.detailed


@pytest.mark.asyncio
async def test_summarize_strips_markdown_code_fences():
    """Haiku sometimes wraps JSON in ```json ... ```."""
    haiku = _make_haiku(f"```json\n{_OK_RESPONSE}\n```")
    summary = await summarize_transcript("transcript", haiku_caller=haiku)
    assert summary.tldr.startswith("Added")
    assert summary.detailed is not None


@pytest.mark.asyncio
async def test_summarize_finds_json_in_prose():
    """Haiku sometimes adds a sentence before the JSON object."""
    haiku = _make_haiku(f"Here you go!\n\n{_OK_RESPONSE}\n\nLet me know.")
    summary = await summarize_transcript("transcript", haiku_caller=haiku)
    assert summary.detailed is not None


@pytest.mark.asyncio
async def test_summarize_raises_on_unparseable_response():
    haiku = _make_haiku("not json at all just a sentence")
    with pytest.raises(SummarizerError):
        await summarize_transcript("transcript", haiku_caller=haiku)


@pytest.mark.asyncio
async def test_summarize_raises_on_empty_response():
    haiku = _make_haiku("")
    with pytest.raises(SummarizerError):
        await summarize_transcript("t", haiku_caller=haiku)


@pytest.mark.asyncio
async def test_summarize_normalises_bad_status_to_done():
    bad = json.dumps({"tldr": "x", "standard": "y", "status": "wibble"})
    haiku = _make_haiku(bad)
    summary = await summarize_transcript("t", haiku_caller=haiku)
    assert summary.detailed is not None
    assert summary.detailed.status == "done"


@pytest.mark.asyncio
async def test_summarize_drops_evidence_rows_missing_required_fields():
    bad = json.dumps({
        "tldr": "x", "standard": "y", "status": "done",
        "evidence": {
            "files_touched": [
                {"path": "ok.py", "action": "created"},
                {"action": "created"},   # missing path — drop
                "not even a dict",
            ],
            "tests_run": [
                {"command": "pytest", "exit_code": 0},
                {"exit_code": 1},         # missing command — drop
            ],
        },
    })
    haiku = _make_haiku(bad)
    summary = await summarize_transcript("t", haiku_caller=haiku)
    assert summary.detailed is not None
    assert [f.path for f in summary.detailed.evidence.files_touched] == ["ok.py"]
    assert [t.command for t in summary.detailed.evidence.tests_run] == ["pytest"]


# ── event-stream rendering ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summarize_events_renders_text_tools_and_results():
    haiku = _make_haiku(_OK_RESPONSE)
    events = [
        _event(EventType.AGENT_START),
        _event(EventType.TEXT_DELTA, text="Planning…"),
        _event(EventType.TOOL_USE, tool_name="Edit",
               tool_input={"path": "src/Login.tsx"}),
        _event(EventType.TOOL_RESULT,
               tool_result=[{"type": "text", "text": "ok"}]),
        _event(EventType.COST, input_tokens=100, output_tokens=50, cost_usd=0.001),
        _event(EventType.AGENT_END),
    ]
    summary = await summarize_events(
        events, haiku_caller=haiku, task_description="Add login",
    )
    prompt = haiku.seen["prompt"]   # type: ignore[attr-defined]
    assert "Planning…" in prompt
    assert "[tool Edit]" in prompt
    assert "[result]" in prompt
    assert "Add login" in prompt
    assert summary.detailed is not None


@pytest.mark.asyncio
async def test_summarize_events_trims_long_transcripts():
    haiku = _make_haiku(_OK_RESPONSE)
    long_text = "x " * 20_000
    events = [_event(EventType.TEXT_DELTA, text=long_text)]
    await summarize_events(
        events, haiku_caller=haiku,
        task_description="t",
        max_transcript_chars=2000,
    )
    prompt = haiku.seen["prompt"]   # type: ignore[attr-defined]
    assert "trimmed" in prompt


# ── HTTP wiring ────────────────────────────────────────────────────────────


def test_summarizer_endpoint_runs_through_validation(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from backend.api import summarizer_http as sh
    from backend.main import app

    async def fake_caller(prompt: str) -> str:
        return _OK_RESPONSE

    def fake_build_caller(session_id, **kwargs):
        return fake_caller

    monkeypatch.setattr(sh, "build_caller", fake_build_caller)

    # Make the validators happy — claimed file must exist on disk.
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Login.tsx").write_text("// ok", encoding="utf-8")

    with TestClient(app) as client:
        resp = client.post("/api/summarizer/run", json={
            "session_id": "sess-http",
            "transcript": "[tool Edit]\n[result] wrote file",
            "task_description": "Add login",
            "verify": True,
            "worktree_path": str(tmp_path),
            # The Haiku response claims react-hook-form was installed +
            # ran `npm test` + created src/Login.tsx — supply matching
            # context so the deterministic validators are satisfied.
            "git_changes": [{
                "path": "src/Login.tsx", "is_new": True,
                "lines_added": 42, "lines_removed": 0,
            }],
            "audit_rows": [{"command": "npm test", "exit_code": 0}],
            "installed_packages_after": ["react-hook-form"],
        })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tldr"] == "Added a login form."
    assert body["detailed"]["status"] == "done"
    # Validation block was added because verify=True; passed because the
    # files referenced in the summary exist in worktree_path.
    assert body["verification"]["passed"] is True


def test_summarizer_endpoint_rejects_empty_transcript():
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as client:
        resp = client.post("/api/summarizer/run", json={
            "session_id": "x", "transcript": "   ",
        })
    assert resp.status_code == 400
