"""Phase 2 tests: worktree manager, planner parsing, spawner, reviewer."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.orchestrator.nodes.planner import (
    TeamComposition,
    _fallback_team,
    _parse_team_composition,
)
from backend.orchestrator.nodes.reviewer import ReviewReport, review_and_merge, summarize_results
from backend.orchestrator.nodes.spawner import SpawnedAgent, SpawnPlan, spawn_agents
from backend.orchestrator.state import AgentResult
from backend.worktrees.manager import MergeResult, WorktreeManager


# ── Planner parsing ───────────────────────────────────────────────────────────

def test_parse_valid_team_composition():
    raw = json.dumps({
        "team": [
            {"role": "Thinker", "model": "claude:sonnet", "count": 1, "passive": False},
            {"role": "Builder", "model": "claude:sonnet", "count": 2, "passive": False},
            {"role": "Debugger", "model": "claude:sonnet", "count": 1, "passive": True},
        ],
        "confidence": 0.9,
        "rationale": "standard dev team",
    })
    comp = _parse_team_composition(raw)
    assert len(comp.team) == 3
    assert comp.confidence == 0.9
    assert comp.team[2].passive is True
    assert comp.total_active == 3  # Thinker(1) + Builder(2)


def test_parse_composition_with_markdown_fence():
    raw = "Here's the team:\n```json\n" + json.dumps({
        "team": [{"role": "Builder", "model": "claude:sonnet", "count": 1, "passive": False}],
        "confidence": 0.7,
        "rationale": "simple task",
    }) + "\n```"
    comp = _parse_team_composition(raw)
    assert comp.team[0].role == "Builder"


def test_parse_invalid_json_returns_fallback():
    comp = _parse_team_composition("this is not json at all")
    assert comp.team[0].role == "Builder"
    assert comp.confidence == 0.5


def test_parse_empty_team_returns_fallback():
    comp = _parse_team_composition(json.dumps({"team": [], "confidence": 0.8, "rationale": ""}))
    assert comp.team[0].role == "Builder"


def test_fallback_team_is_single_builder():
    comp = _fallback_team()
    assert len(comp.team) == 1
    assert comp.team[0].role == "Builder"
    assert comp.team[0].passive is False


# ── Spawner concurrency cap ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spawner_respects_concurrency_cap(tmp_path):
    """Spawner should not create more than MAX_CONCURRENT worktrees at once."""
    call_times: list[float] = []
    created_count = 0

    async def _fake_create(agent_id, branch_name=None):
        nonlocal created_count
        created_count += 1
        await asyncio.sleep(0.01)
        return tmp_path / agent_id

    async def _fake_create_agent(*args, **kwargs):
        pass

    from backend.orchestrator.nodes import planner as planner_mod
    comp = _parse_team_composition(json.dumps({
        "team": [{"role": "Builder", "model": "claude:sonnet", "count": 5, "passive": False}],
        "confidence": 0.8, "rationale": "test",
    }))

    with patch("backend.orchestrator.nodes.spawner.WorktreeManager") as MockWTM, \
         patch("backend.orchestrator.nodes.spawner.create_agent", _fake_create_agent):
        mock_mgr = MagicMock()
        mock_mgr.create = _fake_create
        MockWTM.return_value = mock_mgr

        plan = await spawn_agents(
            session_id="sess-cap",
            task="big task",
            composition=comp,
            project_path=str(tmp_path),
            max_concurrent=3,
        )

    assert len(plan.active_agents) == 5
    assert created_count == 5


# ── Reviewer merge logic ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reviewer_marks_failed_agents(tmp_path):
    plan = SpawnPlan(
        session_id="sess-review",
        project_path=str(tmp_path),
        active_agents=[
            SpawnedAgent("ag1", "Builder", "claude:sonnet", str(tmp_path)),
            SpawnedAgent("ag2", "Tester", "claude:sonnet", str(tmp_path)),
        ],
    )
    results: dict[str, AgentResult] = {
        "ag1": AgentResult(agent_id="ag1", status="completed", text_output="done",
                           input_tokens=10, output_tokens=5, cost_usd=0.001, error=None),
        "ag2": AgentResult(agent_id="ag2", status="failed", text_output="",
                           input_tokens=0, output_tokens=0, cost_usd=0.0, error="timeout"),
    }

    with patch("backend.orchestrator.nodes.reviewer.WorktreeManager") as MockWTM:
        mock_mgr = MagicMock()
        mock_mgr.merge_to_main = AsyncMock(
            return_value=MergeResult(success=True, agent_id="ag1", branch="br", commits_merged=1)
        )
        mock_mgr.remove_session_worktrees = AsyncMock()
        MockWTM.return_value = mock_mgr

        report = await review_and_merge(plan, results)

    assert "ag2" in report.failed_agents
    assert len(report.merged) == 1
    assert not report.success


@pytest.mark.asyncio
async def test_reviewer_reports_conflict(tmp_path):
    plan = SpawnPlan(
        session_id="sess-conflict",
        project_path=str(tmp_path),
        active_agents=[SpawnedAgent("ag3", "Builder", "claude:sonnet", str(tmp_path))],
    )
    results: dict[str, AgentResult] = {
        "ag3": AgentResult(agent_id="ag3", status="completed", text_output="x",
                           input_tokens=5, output_tokens=3, cost_usd=0.0005, error=None),
    }

    with patch("backend.orchestrator.nodes.reviewer.WorktreeManager") as MockWTM:
        mock_mgr = MagicMock()
        mock_mgr.merge_to_main = AsyncMock(
            return_value=MergeResult(
                success=False, agent_id="ag3", branch="br",
                commits_merged=0, conflict_files=["main.py"]
            )
        )
        mock_mgr.remove_session_worktrees = AsyncMock()
        MockWTM.return_value = mock_mgr

        report = await review_and_merge(plan, results)

    assert len(report.conflicts) == 1
    assert not report.success


def test_summarize_results():
    results = {
        "ag1": AgentResult(agent_id="ag1", status="completed", text_output="ok",
                           input_tokens=100, output_tokens=50, cost_usd=0.005, error=None),
    }
    report = ReviewReport(session_id="s", notes=["ag1: merged 2 commit(s)"])
    summary = summarize_results(results, report)
    assert "ag1" in summary
    assert "0.0050" in summary


# ── WorktreeManager git integration ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_worktree_create_and_remove(tmp_path):
    """Integration test: actually creates a git repo and worktree."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Init a real git repo
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    mgr = WorktreeManager(session_id="sess-wt", project_path=str(repo))
    # Override the worktree root to use tmp_path
    from backend.worktrees import manager as wt_mod
    original_root = wt_mod.WORKTREES_ROOT
    wt_mod.WORKTREES_ROOT = tmp_path / "worktrees"
    mgr.session_root = tmp_path / "worktrees" / "sess-wt"

    try:
        wt_path = await mgr.create("agent-001")
        assert wt_path.exists()
        assert (wt_path / ".git").exists() or (wt_path.parent / ".git").exists()

        await mgr.remove("agent-001")
        assert not wt_path.exists()
    finally:
        wt_mod.WORKTREES_ROOT = original_root
