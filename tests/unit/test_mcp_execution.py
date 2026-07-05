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
    assert "hive-pw-tester-mcp-0" in args          # per-agent isolation expanded
    assert "/tmp/wt-mcp/.playwright" in args
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
