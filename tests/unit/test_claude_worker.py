"""Tests for ClaudeCLIWorker using a mock subprocess."""
from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.workers.base import EventType, WorkerConfig
from backend.workers.claude_cli import ClaudeCLIWorker


def _make_config() -> WorkerConfig:
    return WorkerConfig(
        agent_id="agent-cli",
        session_id="sess-cli",
        model="claude:sonnet",
        worktree_path="/tmp",
        max_turns=5,
    )


def _build_mock_process(*ndjson_payloads: dict, exit_code: int = 0):
    """Build a mock asyncio subprocess that streams NDJSON lines."""
    ndjson_bytes = b"\n".join(json.dumps(p).encode() for p in ndjson_payloads) + b"\n"

    reader = asyncio.StreamReader()
    reader.feed_data(ndjson_bytes)
    reader.feed_eof()

    mock_proc = MagicMock()
    mock_proc.stdout = reader
    mock_proc.stderr = asyncio.StreamReader()
    mock_proc.stderr.feed_eof()
    mock_proc.pid = 12345
    mock_proc.returncode = exit_code

    async def _wait():
        return exit_code

    mock_proc.wait = _wait
    return mock_proc


@pytest.mark.asyncio
async def test_agent_start_from_init_event():
    proc = _build_mock_process({"type": "system", "subtype": "init"})
    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        events = [e async for e in worker.run("hello", _make_config())]

    types = [e.type for e in events]
    assert EventType.AGENT_START in types
    assert EventType.AGENT_END in types


@pytest.mark.asyncio
async def test_agent_start_carries_subprocess_pid():
    """AGENT_START must be stamped with the subprocess PID so the
    orchestrator can persist it (agents.pid) for restart recovery."""
    proc = _build_mock_process({"type": "system", "subtype": "init"})
    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        events = [e async for e in worker.run("hello", _make_config())]

    starts = [e for e in events if e.type == EventType.AGENT_START]
    assert len(starts) == 1
    assert starts[0].pid == 12345


@pytest.mark.asyncio
async def test_text_done_forwarded():
    """The consolidated `assistant` message arrives as TEXT_DONE so
    consumers don't double-count partial deltas + final text."""
    proc = _build_mock_process(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi there"}]}},
    )
    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        events = [e async for e in worker.run("hello", _make_config())]

    text_events = [e for e in events if e.type == EventType.TEXT_DONE]
    assert text_events[0].text == "Hi there"


@pytest.mark.asyncio
async def test_cost_event_forwarded():
    proc = _build_mock_process(
        {"type": "result", "usage": {"input_tokens": 42, "output_tokens": 7}, "total_cost_usd": 0.001},
    )
    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        events = [e async for e in worker.run("hello", _make_config())]

    cost_events = [e for e in events if e.type == EventType.COST]
    assert cost_events[0].input_tokens == 42
    assert cost_events[0].output_tokens == 7


@pytest.mark.asyncio
async def test_nonzero_exit_yields_agent_error():
    proc = _build_mock_process(exit_code=1)
    stderr_reader = asyncio.StreamReader()
    stderr_reader.feed_data(b"authentication failed\n")
    stderr_reader.feed_eof()
    proc.stderr = stderr_reader

    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        events = [e async for e in worker.run("hello", _make_config())]

    error_events = [e for e in events if e.type == EventType.AGENT_ERROR]
    assert len(error_events) == 1
    assert "authentication failed" in error_events[0].error


@pytest.mark.asyncio
async def test_agent_ids_set_on_all_events():
    proc = _build_mock_process(
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
    )
    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        config = _make_config()
        events = [e async for e in worker.run("hello", config)]

    for e in events:
        assert e.agent_id == config.agent_id
        assert e.session_id == config.session_id


@pytest.mark.asyncio
async def test_idle_timeout_kills_process_and_skips_agent_end(monkeypatch):
    """On stall the worker must kill the hung process group and must NOT
    emit AGENT_END — the stall is a failure, and `await proc.wait()` on a
    hung process would block forever."""
    from backend.workers import stream_parser as sp

    monkeypatch.setattr(sp, "IDLE_TIMEOUT_MS", 50)

    reader = asyncio.StreamReader()  # never feeds data, never EOFs
    mock_proc = MagicMock()
    mock_proc.stdout = reader
    mock_proc.stderr = asyncio.StreamReader()
    mock_proc.pid = 4242

    async def _wait():
        return 0

    mock_proc.wait = _wait

    killed: list[tuple[int, int]] = []
    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("os.getpgid", return_value=999), \
         patch("os.killpg", side_effect=lambda pgid, sig: killed.append((pgid, sig))):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        events = [e async for e in worker.run("hello", _make_config())]

    types = [e.type for e in events]
    assert EventType.AGENT_ERROR in types
    assert EventType.AGENT_END not in types
    assert any("idle-timeout" in (e.error or "") for e in events)
    assert killed, "hung process group was not killed"


@pytest.mark.asyncio
async def test_first_spawn_passes_session_id():
    """B2: a fresh conversation is named with --session-id."""
    proc = _build_mock_process({"type": "system", "subtype": "init"})
    captured: dict = {}

    async def fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        return proc

    cfg = _make_config()
    cfg.claude_session_id = "11111111-2222-3333-4444-555555555555"
    cfg.resume_claude_session = False
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("os.getpgid", return_value=1):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        [e async for e in worker.run("hello", cfg)]

    cmd = captured["cmd"]
    assert "--session-id" in cmd
    assert cmd[cmd.index("--session-id") + 1] == cfg.claude_session_id
    assert "--resume" not in cmd


@pytest.mark.asyncio
async def test_respawn_passes_resume_with_same_uuid():
    """B2: a re-spawned logical agent resumes its conversation."""
    proc = _build_mock_process({"type": "system", "subtype": "init"})
    captured: dict = {}

    async def fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        return proc

    cfg = _make_config()
    cfg.claude_session_id = "11111111-2222-3333-4444-555555555555"
    cfg.resume_claude_session = True
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("os.getpgid", return_value=1):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        [e async for e in worker.run("hello", cfg)]

    cmd = captured["cmd"]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == cfg.claude_session_id
    assert "--session-id" not in cmd


@pytest.mark.asyncio
async def test_mcp_config_flags_present_when_set():
    """C2: --mcp-config + --strict-mcp-config (no global-server leakage)."""
    proc = _build_mock_process({"type": "system", "subtype": "init"})
    captured: dict = {}

    async def fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        return proc

    cfg = _make_config()
    cfg.mcp_config_path = "/x/mcp-configs/sess-agent.json"
    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("os.getpgid", return_value=1):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        [e async for e in worker.run("hello", cfg)]

    cmd = captured["cmd"]
    assert "--mcp-config" in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == cfg.mcp_config_path
    assert "--strict-mcp-config" in cmd


@pytest.mark.asyncio
async def test_no_mcp_flags_without_config():
    proc = _build_mock_process({"type": "system", "subtype": "init"})
    captured: dict = {}

    async def fake_exec(*cmd, **kw):
        captured["cmd"] = list(cmd)
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("os.getpgid", return_value=1):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        [e async for e in worker.run("hello", _make_config())]

    assert "--mcp-config" not in captured["cmd"]
    assert "--strict-mcp-config" not in captured["cmd"]
