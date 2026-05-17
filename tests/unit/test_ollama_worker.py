"""Tests for OllamaWorker using respx to mock HTTP calls."""
from __future__ import annotations

import json
import pytest
import respx
import httpx

from backend.workers.base import EventType, WorkerConfig
from backend.workers.ollama import OllamaWorker


def _make_config(model: str = "ollama:llama3.1") -> WorkerConfig:
    return WorkerConfig(
        agent_id="agent-ollama",
        session_id="sess-ollama",
        model=model,
        worktree_path="/tmp",
    )


def _ndjson_response(*chunks: dict) -> bytes:
    return b"\n".join(json.dumps(c).encode() for c in chunks) + b"\n"


@pytest.mark.asyncio
@respx.mock
async def test_successful_generation():
    chunks = [
        {"model": "llama3.1", "response": "Hello", "done": False},
        {"model": "llama3.1", "response": " world", "done": False},
        {"model": "llama3.1", "response": "", "done": True, "prompt_eval_count": 10, "eval_count": 5},
    ]
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=httpx.Response(200, content=_ndjson_response(*chunks))
    )

    worker = OllamaWorker()
    events = [e async for e in worker.run("say hello", _make_config())]

    types = [e.type for e in events]
    assert EventType.AGENT_START in types
    assert EventType.TEXT_DELTA in types
    assert EventType.COST in types
    assert EventType.AGENT_END in types

    text_events = [e for e in events if e.type == EventType.TEXT_DELTA]
    full_text = "".join(e.text for e in text_events)
    assert full_text == "Hello world"


@pytest.mark.asyncio
@respx.mock
async def test_cost_event_has_zero_usd():
    chunks = [
        {"response": "hi", "done": False},
        {"response": "", "done": True, "prompt_eval_count": 20, "eval_count": 10},
    ]
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=httpx.Response(200, content=_ndjson_response(*chunks))
    )

    worker = OllamaWorker()
    events = [e async for e in worker.run("hi", _make_config())]
    cost_events = [e for e in events if e.type == EventType.COST]

    assert len(cost_events) == 1
    assert cost_events[0].cost_usd == 0.0
    assert cost_events[0].input_tokens == 20
    assert cost_events[0].output_tokens == 10


@pytest.mark.asyncio
async def test_ollama_not_running_yields_agent_error():
    # Use a port that's definitely not listening
    worker = OllamaWorker(base_url="http://localhost:19999")
    events = [e async for e in worker.run("hi", _make_config())]

    error_events = [e for e in events if e.type == EventType.AGENT_ERROR]
    assert len(error_events) == 1
    assert "not reachable" in error_events[0].error.lower() or "connect" in error_events[0].error.lower()


@pytest.mark.asyncio
@respx.mock
async def test_http_error_response_yields_agent_error():
    respx.post("http://localhost:11434/api/generate").mock(
        return_value=httpx.Response(404, content=b'{"error":"model not found"}')
    )

    worker = OllamaWorker()
    events = [e async for e in worker.run("hi", _make_config())]

    error_events = [e for e in events if e.type == EventType.AGENT_ERROR]
    assert len(error_events) == 1
    assert "404" in error_events[0].error


@pytest.mark.asyncio
@respx.mock
async def test_model_name_extracted_from_spec():
    """Ensure 'ollama:qwen2.5' sends 'qwen2.5' to the API, not 'ollama:qwen2.5'."""
    captured_body = {}

    def capture(request):
        captured_body.update(json.loads(request.content))
        chunks = [{"response": "ok", "done": True, "prompt_eval_count": 1, "eval_count": 1}]
        return httpx.Response(200, content=_ndjson_response(*chunks))

    respx.post("http://localhost:11434/api/generate").mock(side_effect=capture)

    worker = OllamaWorker()
    async for _ in worker.run("hi", _make_config("ollama:qwen2.5")):
        pass

    assert captured_body.get("model") == "qwen2.5"
