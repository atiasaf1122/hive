"""B3/B4 — summarizer + validators wired into the worker run loop."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.orchestrator.graph import _execute_worker, review_node
from backend.orchestrator.nodes.spawner import SpawnedAgent
from backend.summarizer.runner import TieredSummary
from backend.validation.schema import CompletionReport, Evidence, FileTouched
from backend.validation.validators import ValidationContext
from backend.workers.base import EventType, HiveEvent


class _FakeWorker:
    """Yields a completed run with some text output."""

    def __init__(self, *a, **kw) -> None: ...

    async def run(self, prompt, config):
        yield HiveEvent(type=EventType.TEXT_DONE, agent_id=config.agent_id,
                        session_id=config.session_id, text="I created auth.ts with the login flow.")
        yield HiveEvent(type=EventType.AGENT_END, agent_id=config.agent_id,
                        session_id=config.session_id)

    async def kill(self, agent_id): ...


def _agent(**kw) -> SpawnedAgent:
    base = dict(agent_id="builder-t-0", role="Builder", model="claude:sonnet",
                worktree_path="/tmp/nonexistent-wt", subtask="Create auth.ts")
    base.update(kw)
    return SpawnedAgent(**base)


def _report_claiming_created(path: str) -> CompletionReport:
    return CompletionReport(
        status="done", description="created the file",
        evidence=Evidence(files_touched=[
            FileTouched(path=path, action="created", what_was_done="new file"),
        ]),
    )


def _tiered(report: CompletionReport | None) -> TieredSummary:
    return TieredSummary(tldr="did the thing", standard="A compact summary of the run.",
                         detailed=report)


@pytest.mark.asyncio
async def test_hallucinated_file_claim_fails_validation_and_trust() -> None:
    """Claimed file creation with no git evidence → validation fails and the
    trust score records the FAILURE even though the process exited clean."""
    trust_calls: list[tuple[str, bool]] = []

    async def fake_trust(worker_id, passed_validation):
        trust_calls.append((worker_id, passed_validation))

    with patch("backend.orchestrator.graph.ClaudeCLIWorker", _FakeWorker), \
         patch("backend.orchestrator.graph._summarize_worker_run",
               new_callable=AsyncMock,
               return_value=_tiered(_report_claiming_created("auth.ts"))), \
         patch("backend.validation.context.collect_git_context",
               new_callable=AsyncMock,
               return_value=ValidationContext(worktree_path="/tmp/x", git_changes=[])), \
         patch("backend.orchestrator.graph._auto_commit_worktree",
               new_callable=AsyncMock, return_value=False), \
         patch("backend.orchestrator.graph.update_agent_status", new_callable=AsyncMock), \
         patch("backend.orchestrator.graph.record_trust_completion",
               side_effect=fake_trust):
        result = await _execute_worker(_agent(), "do it", "sess-t", 10)

    assert result["status"] == "completed"           # process was clean...
    assert result["validation_passed"] is False      # ...but the claim is false
    assert any("auth.ts" in f for f in result["validation_findings"])
    assert "Validation failed" in result["summary"]
    assert trust_calls and trust_calls[0][1] is False  # trust reflects validation


@pytest.mark.asyncio
async def test_validated_claim_passes(tmp_path) -> None:
    """Same claim WITH matching git evidence (and a real file) passes."""
    from backend.validation.validators import GitFileChange

    trust_calls: list[bool] = []

    async def fake_trust(worker_id, passed_validation):
        trust_calls.append(passed_validation)

    (tmp_path / "auth.ts").write_text("export const login = () => {}\n")
    ctx = ValidationContext(
        worktree_path=str(tmp_path),
        git_changes=[GitFileChange(path="auth.ts", is_new=True, is_deleted=False)],
    )
    with patch("backend.orchestrator.graph.ClaudeCLIWorker", _FakeWorker), \
         patch("backend.orchestrator.graph._summarize_worker_run",
               new_callable=AsyncMock,
               return_value=_tiered(_report_claiming_created("auth.ts"))), \
         patch("backend.validation.context.collect_git_context",
               new_callable=AsyncMock, return_value=ctx), \
         patch("backend.orchestrator.graph._auto_commit_worktree",
               new_callable=AsyncMock, return_value=False), \
         patch("backend.orchestrator.graph.update_agent_status", new_callable=AsyncMock), \
         patch("backend.orchestrator.graph.record_trust_completion",
               side_effect=fake_trust):
        result = await _execute_worker(_agent(), "do it", "sess-t2", 10)

    assert result["validation_passed"] is True
    assert result["validation_findings"] == []
    assert trust_calls == [True]
    assert result["summary"].startswith("A compact summary")


@pytest.mark.asyncio
async def test_summarizer_failure_degrades_to_truncated_raw() -> None:
    """Summarizer outage must not fail the turn — raw excerpt is used and
    validation is skipped (no structured report to check)."""
    with patch("backend.orchestrator.graph.ClaudeCLIWorker", _FakeWorker), \
         patch("backend.orchestrator.graph._summarize_worker_run",
               new_callable=AsyncMock, side_effect=RuntimeError("haiku down")), \
         patch("backend.orchestrator.graph._auto_commit_worktree",
               new_callable=AsyncMock, return_value=False), \
         patch("backend.orchestrator.graph.update_agent_status", new_callable=AsyncMock), \
         patch("backend.orchestrator.graph.record_trust_completion",
               new_callable=AsyncMock):
        result = await _execute_worker(_agent(), "do it", "sess-t3", 10)

    assert result["status"] == "completed"
    assert result["summary"].startswith("I created auth.ts")
    assert result["validation_passed"] is None


@pytest.mark.asyncio
async def test_review_history_uses_summary_not_raw_output() -> None:
    """The orchestrator's history entry carries the compact summary."""
    raw = "x" * 20_000
    state = {
        "session_id": "sess-r",
        "spawn_plan": {
            "session_id": "sess-r", "project_path": "/tmp/p",
            "active_agents": [], "passive_agents": [],
        },
        "worker_results": {
            "builder-r-0": {
                "agent_id": "builder-r-0", "status": "completed",
                "text_output": raw, "summary": "Compact: built the API.",
                "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0,
                "error": None,
            },
        },
        "conversation_history": [],
        "last_response": "",
    }
    fake_report = type("R", (), {
        "notes": [], "success": True, "total_commits_merged": 0, "failed_agents": [],
        "merged": [], "conflicts": [],
    })()
    with patch("backend.orchestrator.graph.review_and_merge",
               new_callable=AsyncMock, return_value=fake_report), \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        out = await review_node(state)  # type: ignore[arg-type]

    final = out["conversation_history"][-1]["content"]
    assert "Compact: built the API." in final
    assert raw not in final
    assert len(final) < 2000


# ── B6: LLM review only on conflict / validation failure ───────────────────


def _review_state(results: dict) -> dict:
    return {
        "session_id": "sess-b6",
        "spawn_plan": {
            "session_id": "sess-b6", "project_path": "/tmp/p",
            "active_agents": [], "passive_agents": [],
        },
        "worker_results": results,
        "conversation_history": [],
        "last_response": "",
    }


def _ok_result(aid: str, **kw) -> dict:
    base = {"agent_id": aid, "status": "completed", "text_output": "done",
            "summary": "did it", "input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "error": None, "validation_passed": True,
            "validation_findings": []}
    base.update(kw)
    return base


def _fake_report(conflicts: list | None = None):
    from backend.orchestrator.nodes.reviewer import ReviewReport
    r = ReviewReport(session_id="sess-b6")
    r.conflicts = conflicts or []
    return r


@pytest.mark.asyncio
async def test_clean_merge_skips_llm_review() -> None:
    with patch("backend.orchestrator.graph.review_and_merge",
               new_callable=AsyncMock, return_value=_fake_report()), \
         patch("backend.orchestrator.graph.llm_review",
               new_callable=AsyncMock) as llm, \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        await review_node(_review_state({"a1": _ok_result("a1")}))  # type: ignore[arg-type]
    llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_conflict_triggers_llm_review() -> None:
    from backend.worktrees.manager import MergeResult

    conflict = MergeResult(success=False, agent_id="a1",
                           branch="hive/s/a1", commits_merged=0,
                           conflict_files=["app.py"])
    with patch("backend.orchestrator.graph.review_and_merge",
               new_callable=AsyncMock, return_value=_fake_report([conflict])), \
         patch("backend.orchestrator.graph.llm_review",
               new_callable=AsyncMock, return_value=["LLM review: resolved"]) as llm, \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        out = await review_node(_review_state({"a1": _ok_result("a1")}))  # type: ignore[arg-type]
    llm.assert_awaited_once()
    assert any("LLM review" in n for n in out["review_report"]["notes"])


@pytest.mark.asyncio
async def test_validation_failure_triggers_llm_review() -> None:
    results = {"a1": _ok_result("a1", validation_passed=False,
                                validation_findings=["claims file that doesn't exist"])}
    with patch("backend.orchestrator.graph.review_and_merge",
               new_callable=AsyncMock, return_value=_fake_report()), \
         patch("backend.orchestrator.graph.llm_review",
               new_callable=AsyncMock, return_value=["LLM review: checked"]) as llm, \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        await review_node(_review_state(results))  # type: ignore[arg-type]
    llm.assert_awaited_once()


@pytest.mark.asyncio
async def test_llm_review_runs_opus_and_returns_notes() -> None:
    """The escalation pass itself: opus tier, notes come back."""
    from backend.orchestrator.nodes import reviewer as rmod
    from backend.orchestrator.nodes.spawner import SpawnPlan
    from backend.worktrees.manager import MergeResult

    captured: dict = {}

    class _FakeReviewWorker:
        def __init__(self, *a, **kw): ...

        async def run(self, prompt, config):
            captured["model"] = config.model
            captured["prompt"] = prompt
            yield HiveEvent(type=EventType.TEXT_DONE, agent_id=config.agent_id,
                            session_id=config.session_id, text="Merged a1; all good.")

        async def kill(self, agent_id): ...

    plan = SpawnPlan(session_id="s", project_path="/tmp/p")
    report = _fake_report([MergeResult(success=False, agent_id="a1",
                                       branch="hive/s/a1", commits_merged=0,
                                       conflict_files=["app.py"])])
    with patch.object(rmod, "ClaudeCLIWorker", _FakeReviewWorker, create=True), \
         patch("backend.workers.claude_cli.ClaudeCLIWorker", _FakeReviewWorker):
        notes = await rmod.llm_review(plan, report, {})

    assert captured["model"] == "claude:opus"
    assert "app.py" in captured["prompt"]
    assert notes and "Merged a1" in notes[0]


# ── collect_git_context against real repos (regression: master-named repos) ─


async def _make_repo(path, branch: str) -> None:
    import asyncio

    async def git(*args):
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()

    await git("init", "-b", branch)
    await git("config", "user.name", "t")
    await git("config", "user.email", "t@t")
    (path / "README.md").write_text("hi\n")
    await git("add", "-A")
    await git("commit", "-m", "init")


@pytest.mark.parametrize("branch", ["main", "master"])
@pytest.mark.asyncio
async def test_collect_git_context_finds_committed_work(tmp_path, branch) -> None:
    """The e2e dogfooding run false-negatived every validation claim on a
    master-named repo because the collector only tried 'main'."""
    import asyncio

    from backend.validation.context import collect_git_context

    await _make_repo(tmp_path, branch)

    async def git(*args):
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=str(tmp_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()

    # Simulate an agent branch with a committed new file.
    await git("checkout", "-b", "hive/s/agent-0")
    (tmp_path / "app.py").write_text("print('hi')\n")
    await git("add", "-A")
    await git("commit", "-m", "agent output")

    ctx = await collect_git_context(str(tmp_path))
    paths = {c.path: c for c in ctx.git_changes}
    assert "app.py" in paths, f"committed work invisible on {branch}-named repo"
    assert paths["app.py"].is_new
