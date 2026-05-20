"""Tests for the NDJSON stream parser.

Uses a mock asyncio.StreamReader to simulate claude CLI stdout.
"""
from __future__ import annotations

import asyncio
import json
import pytest

from backend.workers.base import EventType, WorkerConfig
from backend.workers.stream_parser import parse_stream


def _make_config() -> WorkerConfig:
    return WorkerConfig(
        agent_id="agent-test",
        session_id="sess-test",
        model="claude:sonnet",
        worktree_path="/tmp",
    )


def _stream_of(*payloads: dict) -> asyncio.StreamReader:
    """Build a StreamReader that yields NDJSON lines then EOF."""
    reader = asyncio.StreamReader()
    for p in payloads:
        reader.feed_data(json.dumps(p).encode() + b"\n")
    reader.feed_eof()
    return reader


async def _collect(reader: asyncio.StreamReader, config: WorkerConfig) -> list:
    return [e async for e in parse_stream(reader, config)]


@pytest.mark.asyncio
async def test_system_init_yields_agent_start():
    reader = _stream_of({"type": "system", "subtype": "init"})
    events = await _collect(reader, _make_config())
    assert len(events) == 1
    assert events[0].type == EventType.AGENT_START


@pytest.mark.asyncio
async def test_assistant_text_yields_text_done():
    """Consolidated `assistant` messages come AFTER the partial stream_event
    deltas, so the parser emits them as TEXT_DONE (canonical final text).
    Accumulating both as TEXT_DELTA used to double-paragraph the chat."""
    payload = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "hello"}]},
    }
    reader = _stream_of(payload)
    events = await _collect(reader, _make_config())
    assert len(events) == 1
    assert events[0].type == EventType.TEXT_DONE
    assert events[0].text == "hello"


@pytest.mark.asyncio
async def test_assistant_tool_use_yields_tool_use_event():
    payload = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "tid1",
                    "name": "Read",
                    "input": {"path": "/foo.py"},
                }
            ]
        },
    }
    reader = _stream_of(payload)
    events = await _collect(reader, _make_config())
    assert len(events) == 1
    assert events[0].type == EventType.TOOL_USE
    assert events[0].tool_name == "Read"
    assert events[0].tool_use_id == "tid1"


@pytest.mark.asyncio
async def test_assistant_mixed_content_yields_multiple_events():
    payload = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "thinking..."},
                {"type": "tool_use", "id": "tid2", "name": "Write", "input": {}},
            ]
        },
    }
    reader = _stream_of(payload)
    events = await _collect(reader, _make_config())
    assert len(events) == 2
    assert events[0].type == EventType.TEXT_DONE
    assert events[1].type == EventType.TOOL_USE


@pytest.mark.asyncio
async def test_tool_result_event():
    payload = {
        "type": "tool_result",
        "tool_use_id": "tid1",
        "content": "file contents here",
        "is_error": False,
    }
    reader = _stream_of(payload)
    events = await _collect(reader, _make_config())
    assert len(events) == 1
    assert events[0].type == EventType.TOOL_RESULT
    assert events[0].tool_result == "file contents here"
    assert events[0].tool_result_error is False


@pytest.mark.asyncio
async def test_tool_result_error_flag():
    payload = {"type": "tool_result", "tool_use_id": "x", "content": "boom", "is_error": True}
    reader = _stream_of(payload)
    events = await _collect(reader, _make_config())
    assert events[0].tool_result_error is True


@pytest.mark.asyncio
async def test_result_yields_cost_event():
    payload = {
        "type": "result",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "total_cost_usd": 0.0012,
    }
    reader = _stream_of(payload)
    events = await _collect(reader, _make_config())
    assert len(events) == 1
    assert events[0].type == EventType.COST
    assert events[0].input_tokens == 100
    assert events[0].output_tokens == 50
    assert abs(events[0].cost_usd - 0.0012) < 1e-6


@pytest.mark.asyncio
async def test_rate_limit_event():
    payload = {
        "type": "system",
        "subtype": "api_retry",
        "error": {"message": "rate_limit exceeded"},
        "wait_ms": 3000,
    }
    reader = _stream_of(payload)
    events = await _collect(reader, _make_config())
    assert len(events) == 1
    assert events[0].type == EventType.RATE_LIMIT
    assert events[0].retry_after_ms == 3000


@pytest.mark.asyncio
async def test_stream_event_text_delta():
    payload = {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "partial"},
        },
    }
    reader = _stream_of(payload)
    events = await _collect(reader, _make_config())
    assert len(events) == 1
    assert events[0].type == EventType.TEXT_DELTA
    assert events[0].text == "partial"


@pytest.mark.asyncio
async def test_invalid_json_lines_are_skipped():
    reader = asyncio.StreamReader()
    reader.feed_data(b"not json\n")
    reader.feed_data(json.dumps({"type": "system", "subtype": "init"}).encode() + b"\n")
    reader.feed_eof()
    events = await _collect(reader, _make_config())
    # Invalid line skipped; init line processed
    assert len(events) == 1
    assert events[0].type == EventType.AGENT_START


@pytest.mark.asyncio
async def test_partial_chunks_buffered_correctly():
    """Simulate the stream splitting a JSON object across two reads."""
    payload = {"type": "system", "subtype": "init"}
    full = json.dumps(payload).encode() + b"\n"
    mid = len(full) // 2

    reader = asyncio.StreamReader()
    reader.feed_data(full[:mid])
    reader.feed_data(full[mid:])
    reader.feed_eof()

    events = await _collect(reader, _make_config())
    assert len(events) == 1
    assert events[0].type == EventType.AGENT_START
