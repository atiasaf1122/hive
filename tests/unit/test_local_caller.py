"""E4 — LocalCaller + internal-task caller selection: interface swap,
Haiku fallback on Ollama failure, timeout discipline, knob."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.llm.local import LocalCaller, internal_task_caller
from backend.models_local import LocalModel, estimate_vram_mb

_POOL = [LocalModel("qwen3:8b", 5.2, frozenset({"distillation", "summarization"}),
                    "t", estimate_vram_mb(5.2), available=True)]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload=None, exc=None, delay=0.0):
        self._payload, self._exc, self._delay = payload, exc, delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return _FakeResponse(self._payload)


@pytest.mark.asyncio
async def test_local_caller_returns_text_and_logs_zero_cost() -> None:
    payload = {"response": "<think>meh</think>SUMMARY: ok",
               "prompt_eval_count": 100, "eval_count": 50}
    costs: list[tuple] = []

    async def fake_write_cost(sid, aid, tin, tout, cost, **kw):
        costs.append((sid, aid, tin, tout, cost))

    with patch("backend.llm.local.httpx.AsyncClient",
               return_value=_FakeClient(payload)), \
         patch("backend.persistence.events.write_cost", fake_write_cost):
        caller = LocalCaller(model="qwen3:8b", session_id="s", agent_id_prefix="sum")
        out = await caller("summarize this")

    assert out == "SUMMARY: ok"          # think-span stripped
    assert costs == [("s", "sum-qwen3:8b-s-1", 100, 50, 0.0)]


@pytest.mark.asyncio
async def test_local_caller_times_out_hard() -> None:
    with patch("backend.llm.local.httpx.AsyncClient",
               return_value=_FakeClient({"response": "late"}, delay=2.0)):
        caller = LocalCaller(model="qwen3:8b", session_id="s", timeout_s=0.05)
        with pytest.raises(asyncio.TimeoutError):
            await caller("slow prompt")


@pytest.mark.asyncio
async def test_distillation_defaults_to_haiku_unless_forced() -> None:
    """E4.3 verdict: a local distiller would gate its own confabulations."""

    class _FakeHaiku:
        def __init__(self, *a, **kw): ...

        async def __call__(self, prompt):
            return "haiku"

    with patch("backend.models_local.discover_local_models",
               new=AsyncMock(return_value=_POOL)), \
         patch("backend.llm.haiku.HaikuCaller", _FakeHaiku):
        _, label = await internal_task_caller("distillation", "s", "x")
        assert label == "claude:haiku"
        with patch.dict("os.environ", {"HIVE_LOCAL_INTERNAL": "on"}):
            _, label = await internal_task_caller("distillation", "s", "x")
        assert label == "local:qwen3:8b+haiku-fallback"


@pytest.mark.asyncio
async def test_internal_caller_prefers_local_and_falls_back_on_failure() -> None:
    haiku_calls: list[str] = []

    class _FakeHaiku:
        def __init__(self, *a, **kw): ...

        async def __call__(self, prompt):
            haiku_calls.append(prompt)
            return "haiku answer"

    with patch("backend.models_local.discover_local_models",
               new=AsyncMock(return_value=_POOL)), \
         patch("backend.llm.local.LocalCaller") as local_cls, \
         patch("backend.llm.haiku.HaikuCaller", _FakeHaiku):
        local_cls.return_value = AsyncMock(side_effect=RuntimeError("ollama died"))
        caller, label = await internal_task_caller("summarization", "s", "summarizer")
        out = await caller("summarize this")

    assert label == "local:qwen3:8b+haiku-fallback"
    assert out == "haiku answer" and haiku_calls == ["summarize this"]


@pytest.mark.asyncio
async def test_internal_caller_knob_off_is_haiku_only() -> None:
    class _FakeHaiku:
        def __init__(self, *a, **kw): ...

        async def __call__(self, prompt):
            return "haiku"

    with patch.dict("os.environ", {"HIVE_LOCAL_INTERNAL": "off"}), \
         patch("backend.models_local.discover_local_models",
               new=AsyncMock(side_effect=AssertionError("must not discover"))), \
         patch("backend.llm.haiku.HaikuCaller", _FakeHaiku):
        caller, label = await internal_task_caller("distillation", "s", "x")
    assert label == "claude:haiku"


@pytest.mark.asyncio
async def test_internal_caller_no_pool_uses_haiku() -> None:
    class _FakeHaiku:
        def __init__(self, *a, **kw): ...

        async def __call__(self, prompt):
            return "haiku"

    with patch("backend.models_local.discover_local_models",
               new=AsyncMock(return_value=[])), \
         patch("backend.llm.haiku.HaikuCaller", _FakeHaiku):
        caller, label = await internal_task_caller("summarization", "s", "x")
    assert label == "claude:haiku"
