"""E2 — hybrid routing: local: tiers in briefs, file-block worker harness,
VRAM-full fallback, planner digest gating."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.models_local import LocalModel, estimate_vram_mb
from backend.orchestrator.nodes.planner import _parse_composition_dict
from backend.workers.base import EventType, HiveEvent, WorkerConfig
from backend.workers.ollama import OllamaWorker, _apply_file_blocks


def _plan(team):
    return {"response": "ok", "team": team, "confidence": 0.9, "rationale": "r"}


# ── schema: local: tier + fallback ──────────────────────────────────────────


def test_parser_normalizes_local_prefix_and_fallback() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "local:qwen3-coder:30b", "subtask": "x",
         "fallback": "sonnet"},
    ]))
    assert comp.team[0].model == "ollama:qwen3-coder:30b"
    assert comp.team[0].fallback == "sonnet"


def test_parser_fallback_defaults_to_haiku() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "ollama:qwen3:8b", "subtask": "x"},
    ]))
    assert comp.team[0].fallback == "haiku"


# ── file-block harness ──────────────────────────────────────────────────────


def test_apply_file_blocks_writes_inside_worktree(tmp_path) -> None:
    text = (
        "I created the module.\n"
        "<<<FILE: pkg/mod.py>>>\ndef f():\n    return 1\n<<<END FILE>>>\n"
        "Also docs.\n"
        "<<<FILE: README.md>>>\n# hi\n<<<END FILE>>>"
    )
    written, refused = _apply_file_blocks(text, tmp_path)
    assert written == ["pkg/mod.py", "README.md"] and refused == []
    assert (tmp_path / "pkg/mod.py").read_text() == "def f():\n    return 1\n"


def test_apply_file_blocks_refuses_escapes(tmp_path) -> None:
    text = (
        "<<<FILE: ../evil.txt>>>x<<<END FILE>>>\n"
        "<<<FILE: /etc/passwd>>>x<<<END FILE>>>"
    )
    written, refused = _apply_file_blocks(text, tmp_path)
    assert written == [] and set(refused) == {"../evil.txt", "/etc/passwd"}
    assert not (tmp_path.parent / "evil.txt").exists()


class _FakeStreamResponse:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, *a, **kw):
        return _FakeStreamResponse(self._lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_ollama_worker_applies_blocks_and_reports(tmp_path) -> None:
    reply = ("<think>plan it</think>Done.\n"
             "<<<FILE: greet.py>>>\ndef greet():\n    return 'hello'\n<<<END FILE>>>")
    lines = [json.dumps({"response": reply}),
             json.dumps({"done": True, "prompt_eval_count": 5, "eval_count": 9})]
    worker = OllamaWorker(base_url="http://fake:11434")
    config = WorkerConfig(agent_id="a", session_id="s",
                          model="ollama:qwen3-coder:30b",
                          worktree_path=str(tmp_path), max_turns=10)
    with patch("backend.workers.ollama.httpx.AsyncClient",
               return_value=_FakeClient(lines)):
        events = [e async for e in worker.run("write greet.py", config)]

    types = [e.type for e in events]
    assert types[0] == EventType.AGENT_START and types[-1] == EventType.AGENT_END
    tool = next(e for e in events if e.type == EventType.TOOL_USE)
    assert tool.tool_name == "write_file" and tool.tool_input == {"path": "greet.py"}
    assert (tmp_path / "greet.py").read_text().startswith("def greet()")
    done = next(e for e in events if e.type == EventType.TEXT_DONE)
    assert "<think>" not in done.text and "[wrote greet.py]" in done.text
    cost = next(e for e in events if e.type == EventType.COST)
    assert cost.cost_usd == 0.0 and cost.output_tokens == 9


# ── VRAM-full fallback at execute time ──────────────────────────────────────


@pytest.mark.asyncio
async def test_vram_full_falls_back_to_declared_tier(tmp_path) -> None:
    from backend.orchestrator import graph as gmod
    from backend.orchestrator.nodes.spawner import SpawnedAgent

    agent = SpawnedAgent(
        agent_id="b-0", role="Builder", model="ollama:qwen3-coder:30b",
        worktree_path=str(tmp_path), subtask="x", fallback="sonnet")

    # The summarizer ALSO spins a (fake) worker after the run — capture a
    # list and assert on the first (the actual agent run).
    captured_models: list[str] = []

    class _FakeClaude:
        def __init__(self, *a, **kw): ...

        async def run(self, prompt, config):
            captured_models.append(config.model)
            yield HiveEvent(type=EventType.TEXT_DONE, agent_id=config.agent_id,
                            session_id=config.session_id, text="done")

        async def kill(self, agent_id): ...

    fallback_events: list = []

    async def fake_write_event(ev, **kw):
        if ev.type == EventType.MODEL_FALLBACK:
            fallback_events.append(ev)

    with patch.object(gmod, "_reserve_local_vram",
                      new=AsyncMock(return_value=(False, "insufficient VRAM headroom at spawn"))), \
         patch.object(gmod, "ClaudeCLIWorker", _FakeClaude), \
         patch.object(gmod, "write_event", fake_write_event), \
         patch.object(gmod, "_emit_to_ws", new=AsyncMock()), \
         patch.object(gmod, "update_agent_status", new=AsyncMock()), \
         patch.object(gmod, "_auto_commit_worktree", new=AsyncMock()), \
         patch.object(gmod, "summarize_worker_run", new=AsyncMock(return_value=None), create=True):
        result = await gmod._execute_worker(agent, "p", "sess", 10)

    assert agent.model == "claude:sonnet"
    assert captured_models and captured_models[0] == "claude:sonnet"
    assert len(fallback_events) == 1
    payload = fallback_events[0].raw_payload
    assert payload["from"] == "ollama:qwen3-coder:30b" and payload["to"] == "claude:sonnet"
    assert "VRAM" in payload["reason"]
    assert result["status"] == "completed"


# ── F0.4: RAM pressure refuses local spawns (same fallback path) ────────────


@pytest.mark.asyncio
async def test_ram_pressure_refuses_local_spawn_with_reason() -> None:
    from backend.orchestrator import graph as gmod
    from backend.orchestrator.nodes.spawner import SpawnedAgent

    agent = SpawnedAgent(agent_id="b-0", role="Builder",
                         model="ollama:qwen3:8b", worktree_path="/tmp")

    class _VM:
        available = 1 * 2**30   # 1GB free < 4GB floor

    with patch("psutil.virtual_memory", return_value=_VM()):
        fits, reason = await gmod._reserve_local_vram(agent)
    assert fits is False
    assert reason.startswith("ram_pressure:")


@pytest.mark.asyncio
async def test_ram_ok_proceeds_to_vram_check() -> None:
    from unittest.mock import AsyncMock as AM

    from backend.models_local import LocalModel, estimate_vram_mb
    from backend.orchestrator import graph as gmod
    from backend.orchestrator.nodes.spawner import SpawnedAgent

    agent = SpawnedAgent(agent_id="b-0", role="Builder",
                         model="ollama:qwen3:8b", worktree_path="/tmp")
    pool = [LocalModel("qwen3:8b", 5.2, frozenset({"coding"}), "t",
                       estimate_vram_mb(5.2), available=True)]

    class _VM:
        available = 16 * 2**30

    with patch("psutil.virtual_memory", return_value=_VM()), \
         patch("backend.models_local.discover_local_models",
               new=AM(return_value=pool)), \
         patch("backend.resources.vram_manager.reserve",
               new=AM(return_value=True)):
        fits, reason = await gmod._reserve_local_vram(agent)
    assert fits is True and reason == ""


@pytest.mark.asyncio
async def test_vram_full_reason_when_ram_ok() -> None:
    from unittest.mock import AsyncMock as AM

    from backend.models_local import LocalModel, estimate_vram_mb
    from backend.orchestrator import graph as gmod
    from backend.orchestrator.nodes.spawner import SpawnedAgent

    agent = SpawnedAgent(agent_id="b-0", role="Builder",
                         model="ollama:qwen3-coder:30b", worktree_path="/tmp")
    pool = [LocalModel("qwen3-coder:30b", 18.6, frozenset({"coding"}), "t",
                       estimate_vram_mb(18.6), available=True)]

    class _VM:
        available = 16 * 2**30

    with patch("psutil.virtual_memory", return_value=_VM()), \
         patch("backend.models_local.discover_local_models",
               new=AM(return_value=pool)), \
         patch("backend.resources.vram_manager.reserve",
               new=AM(return_value=False)):
        fits, reason = await gmod._reserve_local_vram(agent)
    assert fits is False and "VRAM" in reason


# ── planner digest gating ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_digest_lists_only_available_models() -> None:
    from backend.orchestrator.nodes.planner import _local_models_digest

    pool = [
        LocalModel("qwen3-coder:30b", 18.6, frozenset({"coding"}), "tier",
                   estimate_vram_mb(18.6), available=True),
        LocalModel("qwen3:8b", 5.2, frozenset({"distillation"}), "tier",
                   estimate_vram_mb(5.2), available=False,
                   unavailable_reason="vram"),
    ]
    with patch("backend.models_local.discover_local_models",
               new=AsyncMock(return_value=pool)):
        digest = await _local_models_digest()
    assert "local:qwen3-coder:30b" in digest
    assert "qwen3:8b" not in digest
    assert "$0" in digest


@pytest.mark.asyncio
async def test_local_digest_empty_when_no_pool() -> None:
    from backend.orchestrator.nodes.planner import _local_models_digest

    with patch("backend.models_local.discover_local_models",
               new=AsyncMock(return_value=[])):
        assert await _local_models_digest() == ""
