"""E1 — local model discovery, capability catalog, VRAM gating, degradation."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.models_local import (
    LocalModel,
    best_local_for,
    discover_local_models,
    estimate_vram_mb,
    resolve_capabilities,
)
from backend.resources import GPUInfo, VRAMManager, VRAMSnapshot


# ── capability resolution ───────────────────────────────────────────────────


def test_coder_family_gets_coding_capability() -> None:
    caps, tier = resolve_capabilities("qwen3-coder:30b", 18.6)
    assert "coding" in caps
    assert "sonnet" in tier


def test_general_family_gets_meta_capabilities_not_coding() -> None:
    caps, _ = resolve_capabilities("qwen3:8b", 5.2)
    assert "distillation" in caps and "summarization" in caps
    assert "coding" not in caps


def test_small_model_downgraded_to_classification() -> None:
    caps, tier = resolve_capabilities("llama3.2:3b", 2.0)
    assert "classification" in caps
    assert "coding" not in caps and "distillation" not in caps
    assert "small" in tier


def test_unknown_family_is_conservative() -> None:
    caps, _ = resolve_capabilities("some-experimental-llm:70b", 40.0)
    assert caps == frozenset({"summarization", "classification"})


# ── VRAM snapshot math ──────────────────────────────────────────────────────


def _two_3090s(used0: int = 1000, used1: int = 0) -> VRAMSnapshot:
    return VRAMSnapshot(gpus=[
        GPUInfo(index=0, name="RTX 3090", total_mb=24576, used_mb=used0),
        GPUInfo(index=1, name="RTX 3090", total_mb=24576, used_mb=used1),
    ])


def test_headroom_applies_utilization_margin_and_reservations() -> None:
    snap = _two_3090s()
    budget = int((24576 * 2) * 0.85)
    assert snap.headroom_mb == budget - 1000
    snap.reserved_mb = 20000
    assert snap.headroom_mb == budget - 1000 - 20000


def test_used_percent_is_worst_gpu() -> None:
    snap = _two_3090s(used0=20000, used1=100)
    assert snap.used_percent == pytest.approx(100.0 * 20000 / 24576)


@pytest.mark.asyncio
async def test_reserve_refuses_when_over_headroom_and_releases() -> None:
    manager = VRAMManager()
    gpus = _two_3090s().gpus
    with patch("backend.resources._query_nvidia_smi",
               new=AsyncMock(return_value=gpus)):
        assert await manager.reserve("a", 30000) is True     # fits 40.7GB budget
        assert await manager.reserve("b", 30000) is False    # 30k reserved already
        manager.release("a")
        assert await manager.reserve("b", 30000) is True


@pytest.mark.asyncio
async def test_reserve_optimistic_when_vram_unknown() -> None:
    manager = VRAMManager()
    with patch.object(manager, "snapshot", new=AsyncMock(return_value=None)):
        assert await manager.reserve("a", 10**9) is True


# ── discovery ───────────────────────────────────────────────────────────────


_TAGS = {"models": [
    {"name": "qwen3-coder:30b", "size": 18_600_000_000},
    {"name": "qwen3:8b", "size": 5_200_000_000},
]}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload=None, exc=None, ps_payload=None):
        self._payload, self._exc = payload, exc
        self._ps = ps_payload if ps_payload is not None else {"models": []}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        if self._exc:
            raise self._exc
        if url.endswith("/api/ps"):
            return _FakeResponse(self._ps)
        return _FakeResponse(self._payload)


@pytest.mark.asyncio
async def test_discovery_parses_tags_and_gates_on_vram() -> None:
    tight = _two_3090s(used0=24000, used1=20000)   # headroom ~ -2.2GB → 0
    with patch("backend.models_local.httpx.AsyncClient",
               return_value=_FakeClient(_TAGS)), \
         patch("backend.resources.vram_manager.snapshot",
               new=AsyncMock(return_value=tight)):
        models = await discover_local_models(base_url="http://x:11434")
    by_name = {m.name: m for m in models}
    assert not by_name["qwen3-coder:30b"].available
    assert "headroom" in by_name["qwen3-coder:30b"].unavailable_reason


@pytest.mark.asyncio
async def test_discovery_available_with_free_vram() -> None:
    with patch("backend.models_local.httpx.AsyncClient",
               return_value=_FakeClient(_TAGS)), \
         patch("backend.resources.vram_manager.snapshot",
               new=AsyncMock(return_value=_two_3090s())):
        models = await discover_local_models(base_url="http://x:11434")
    assert all(m.available for m in models)
    assert {m.name for m in models} == {"qwen3-coder:30b", "qwen3:8b"}


@pytest.mark.asyncio
async def test_resident_model_available_despite_no_headroom() -> None:
    """F0.4: a model already loaded in VRAM must not be double-counted —
    its own residency was eating the headroom it was judged against."""
    tight = _two_3090s(used0=24000, used1=20000)
    ps = {"models": [{"name": "qwen3-coder:30b"}]}
    with patch("backend.models_local.httpx.AsyncClient",
               return_value=_FakeClient(_TAGS, ps_payload=ps)), \
         patch("backend.resources.vram_manager.snapshot",
               new=AsyncMock(return_value=tight)):
        models = await discover_local_models(base_url="http://x:11434")
    by_name = {m.name: m for m in models}
    assert by_name["qwen3-coder:30b"].available and by_name["qwen3-coder:30b"].resident
    assert not by_name["qwen3:8b"].available          # not resident, no headroom


@pytest.mark.asyncio
async def test_discovery_degrades_to_empty_when_ollama_down() -> None:
    with patch("backend.models_local.httpx.AsyncClient",
               return_value=_FakeClient(exc=ConnectionError("refused"))):
        assert await discover_local_models(base_url="http://x:11434") == []


# ── selection helper ────────────────────────────────────────────────────────


def test_best_local_for_prefers_largest_capable_available() -> None:
    pool = [
        LocalModel("qwen3:8b", 5.2, frozenset({"distillation"}), "t",
                   estimate_vram_mb(5.2), available=True),
        LocalModel("qwen3-coder:30b", 18.6, frozenset({"coding"}), "t",
                   estimate_vram_mb(18.6), available=True),
        LocalModel("big-coder:70b", 40.0, frozenset({"coding"}), "t",
                   estimate_vram_mb(40.0), available=False),
    ]
    assert best_local_for("coding", pool).name == "qwen3-coder:30b"
    assert best_local_for("distillation", pool).name == "qwen3:8b"
    assert best_local_for("browsing", pool) is None
