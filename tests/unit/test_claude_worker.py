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
async def test_text_delta_forwarded():
    proc = _build_mock_process(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi there"}]}},
    )
    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        worker = ClaudeCLIWorker(oauth_token="test-token")
        events = [e async for e in worker.run("hello", _make_config())]

    text_events = [e for e in events if e.type == EventType.TEXT_DELTA]
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
