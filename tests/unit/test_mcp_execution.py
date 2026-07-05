"""C2/C4 — per-agent MCP config generation + preflight fail-fast in the run loop."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.orchestrator.graph import _execute_worker
from backend.orchestrator.nodes.spawner import SpawnedAgent
from backend.workers.base import EventType, HiveEvent


class _CapturingWorker:
    """Fake ClaudeCLIWorker capturing the WorkerConfig it receives."""

    captured: dict = {}

    def __init__(self, *a, **kw) -> None: ...

    async def run(self, prompt, config):
        _CapturingWorker.captured["config"] = config
        yield HiveEvent(type=EventType.TEXT_DONE, agent_id=config.agent_id,
                        session_id=config.session_id, text="ok")

    async def kill(self, agent_id): ...


def _agent(**kw) -> SpawnedAgent:
    base = dict(agent_id="tester-mcp-0", role="Tester", model="claude:sonnet",
                worktree_path="/tmp/wt-mcp", subtask="verify in browser",
                mcp_servers=["playwright"])
    base.update(kw)
    return SpawnedAgent(**base)


def _common_patches():
    return [
        patch("backend.orchestrator.graph.ClaudeCLIWorker", _CapturingWorker),
        patch("backend.orchestrator.graph._summarize_worker_run",
              new_callable=AsyncMock, side_effect=RuntimeError("skip")),
        patch("backend.orchestrator.graph._auto_commit_worktree",
              new_callable=AsyncMock, return_value=False),
        patch("backend.orchestrator.graph.update_agent_status", new_callable=AsyncMock),
        patch("backend.orchestrator.graph.record_trust_completion", new_callable=AsyncMock),
        patch("backend.orchestrator.graph.write_event", new_callable=AsyncMock),
    ]


@pytest.mark.asyncio
async def test_mcp_agent_gets_config_file_and_path() -> None:
    """The agent's brief servers land in a per-agent config file with
    isolation placeholders expanded, and the worker receives its path."""
    _CapturingWorker.captured = {}
    ws_events: list[dict] = []

    async def fake_ws(session_id, payload):
        ws_events.append(payload)

    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
         patch("backend.orchestrator.graph._emit_to_ws", side_effect=fake_ws), \
         patch("backend.mcp.catalog._node_major", return_value=22):
        result = await _execute_worker(_agent(), "prompt", "sess-mcp", 10)

    assert result["status"] == "completed"
    cfg = _CapturingWorker.captured["config"]
    assert cfg.mcp_config_path, "worker did not receive an mcp config path"
    data = json.loads(Path(cfg.mcp_config_path).read_text())
    args = " ".join(data["mcpServers"]["playwright"]["args"])
    assert "--isolated" in args                    # per-agent in-memory profile
    assert "/tmp/wt-mcp/.playwright" in args       # output dir expanded
    # C4: attachment event visible on the stream
    assert any(e.get("type") == "mcp_servers_attached"
               and e.get("servers") == ["playwright"] for e in ws_events)


@pytest.mark.asyncio
async def test_agent_without_servers_gets_no_config() -> None:
    _CapturingWorker.captured = {}
    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        await _execute_worker(_agent(mcp_servers=[]), "prompt", "sess-mcp2", 10)

    assert _CapturingWorker.captured["config"].mcp_config_path is None


@pytest.mark.asyncio
async def test_preflight_failure_fails_spawn_fast_with_named_requirement(monkeypatch) -> None:
    """Missing requirement → the worker NEVER runs; the error names it."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    _CapturingWorker.captured = {}
    ws_events: list[dict] = []

    async def fake_ws(session_id, payload):
        ws_events.append(payload)

    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
         patch("backend.orchestrator.graph._emit_to_ws", side_effect=fake_ws):
        result = await _execute_worker(
            _agent(mcp_servers=["github"]), "prompt", "sess-mcp3", 10)

    assert result["status"] == "failed"
    assert "GITHUB_TOKEN" in result["error"]
    assert "config" not in _CapturingWorker.captured   # spawn never happened
    assert any(e.get("type") == "mcp_preflight_failed" for e in ws_events)


@pytest.mark.asyncio
async def test_unknown_server_id_fails_spawn() -> None:
    """Defense in depth: parse-time drops unknown ids, but if one reaches
    the spawn path it must fail with a clear message."""
    _CapturingWorker.captured = {}
    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock):
        result = await _execute_worker(
            _agent(mcp_servers=["doesnotexist"]), "prompt", "sess-mcp4", 10)

    assert result["status"] == "failed"
    assert "doesnotexist" in result["error"]


# ── C3: planner assignment ──────────────────────────────────────────────────


def test_plan_with_mcp_servers_parses() -> None:
    from backend.orchestrator.nodes.planner import _parse_composition_dict

    comp = _parse_composition_dict({
        "team": [
            {"role": "Tester", "model": "claude:sonnet",
             "subtask": "verify in a real browser", "mcp_servers": ["playwright"]},
            {"role": "Builder", "model": "claude:sonnet", "subtask": "write app"},
        ],
        "confidence": 0.9, "rationale": "t",
    })
    assert comp.team[0].mcp_servers == ["playwright"]
    assert comp.team[1].mcp_servers == []


def test_unknown_server_id_dropped_with_warning(caplog) -> None:
    from backend.orchestrator.nodes.planner import _parse_composition_dict

    with caplog.at_level("WARNING"):
        comp = _parse_composition_dict({
            "team": [{"role": "Tester", "model": "claude:sonnet",
                      "subtask": "x", "mcp_servers": ["playwright", "not-a-server"]}],
            "confidence": 0.9, "rationale": "t",
        })
    assert comp.team[0].mcp_servers == ["playwright"]
    assert any("not-a-server" in r.message for r in caplog.records)


def test_planner_prompt_contains_catalog_digest() -> None:
    from backend.mcp.catalog import CATALOG
    from backend.orchestrator.nodes.planner import _INSTRUCTIONS

    for sid in CATALOG:
        assert sid in _INSTRUCTIONS, f"{sid} missing from planner digest"
    assert "Assign servers ONLY when the subtask truly needs them" in _INSTRUCTIONS
    assert "__MCP_DIGEST__" not in _INSTRUCTIONS


@pytest.mark.asyncio
async def test_respawn_with_added_server_resumes_and_reequips() -> None:
    """The orchestrator adds a server on re-engagement: same logical agent
    id → --resume (B2) + a fresh config carrying the new server (C3).

    The uuid store itself is covered in test_persistence; here a stateful
    fake mimics mint-then-reuse so we test the spawn-path wiring.
    """
    store: dict[str, str] = {}

    async def fake_get_or_create(agent_id, db_path=None):
        if agent_id in store:
            return store[agent_id], True
        store[agent_id] = "11111111-2222-3333-4444-555555555555"
        return store[agent_id], False

    _CapturingWorker.captured = {}
    patches = _common_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], \
         patch("backend.orchestrator.graph._emit_to_ws", new_callable=AsyncMock), \
         patch("backend.persistence.events.get_or_create_claude_session",
               side_effect=fake_get_or_create), \
         patch("backend.mcp.catalog._node_major", return_value=22):
        # First spawn: no servers.
        await _execute_worker(
            _agent(agent_id="tester-s-re-0", mcp_servers=[]), "p", "s-re", 10)
        first_cfg = _CapturingWorker.captured["config"]
        # Re-engagement: orchestrator added playwright to the brief.
        await _execute_worker(
            _agent(agent_id="tester-s-re-0", mcp_servers=["playwright"]), "p2", "s-re", 10)
        second_cfg = _CapturingWorker.captured["config"]

    assert first_cfg.mcp_config_path is None
    assert first_cfg.resume_claude_session is False
    assert second_cfg.mcp_config_path is not None
    assert second_cfg.resume_claude_session is True          # kept its context
    assert second_cfg.claude_session_id == first_cfg.claude_session_id
    data = json.loads(Path(second_cfg.mcp_config_path).read_text())
    assert "playwright" in data["mcpServers"]


def test_equipped_agent_prompt_mentions_its_servers() -> None:
    """C5 lesson: the agent must be TOLD it has MCP tools, or it reinstalls
    the capability from scratch via Bash (observed in the e2e run)."""
    from backend.orchestrator.graph import _build_agent_prompt

    prompt = _build_agent_prompt(
        _agent(mcp_servers=["playwright"]), goal="g", pending="g")
    assert "Equipment" in prompt
    assert "playwright" in prompt
    assert "do NOT install" in prompt

    bare = _build_agent_prompt(_agent(mcp_servers=[]), goal="g", pending="g")
    assert "Equipment" not in bare


def test_absolute_claim_path_matches_relative_git_change() -> None:
    """C5 false positive: worker claimed the absolute worktree path while
    git reported the repo-relative one."""
    from backend.validation.validators import (
        GitFileChange, ValidationContext, _git_change_for)

    ctx = ValidationContext(
        worktree_path="/home/u/.hive/worktrees/s/a",
        git_changes=[GitFileChange(path="index.html", is_new=True, is_deleted=False)],
    )
    hit = _git_change_for("/home/u/.hive/worktrees/s/a/index.html", ctx)
    assert hit is not None and hit.path == "index.html"
    assert _git_change_for("index.html", ctx) is not None
    assert _git_change_for("/elsewhere/other.html", ctx) is None
