"""Part 2 — three-layer local-model resolver: /api/show metadata + cache,
inference table (incl. fictional future families), audition overrides,
new-digest nudge."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.models_local import (
    _extract_show_metadata,
    _get_probe,
    _store_probe,
    audition_model,
    discover_local_models,
    estimate_params_b,
    record_measured,
    resolve_model,
    unauditioned_models,
)
from backend.persistence.db import init_db
from backend.resources import GPUInfo, VRAMSnapshot


# ── /api/show parsing ─────────────────────────────────────────────────────────

def test_extract_show_metadata_reads_details_and_capabilities() -> None:
    payload = {
        "details": {"family": "qwen3moe", "families": ["qwen3moe"],
                    "parameter_size": "30.5B", "quantization_level": "Q4_K_M"},
        "capabilities": ["completion", "tools", "vision"],
    }
    meta = _extract_show_metadata(payload)
    assert meta["family"] == "qwen3moe"
    assert meta["parameter_size"] == "30.5B"
    assert meta["quantization"] == "Q4_K_M"
    assert "vision" in meta["capabilities"]


def test_extract_show_metadata_tolerates_sparse_payloads() -> None:
    meta = _extract_show_metadata({})
    assert meta == {"family": "", "families": [], "parameter_size": "",
                    "quantization": "", "capabilities": []}


@pytest.mark.asyncio
async def test_probe_cache_roundtrip() -> None:
    await init_db()
    assert await _get_probe("m1:7b", "sha:aaa") is None
    await _store_probe("m1:7b", "sha:aaa", {"family": "m1"})
    cached = await _get_probe("m1:7b", "sha:aaa")
    assert cached == {"metadata": {"family": "m1"}, "measured": None}
    # A new digest is a cache miss — probe once per model VERSION.
    assert await _get_probe("m1:7b", "sha:bbb") is None


# ── parameter estimation + inference table ───────────────────────────────────

def test_params_from_metadata_beats_name_and_size() -> None:
    assert estimate_params_b("weird-name", 5.0, {"parameter_size": "30.5B"}) == 30.5
    assert estimate_params_b("thing:8b", 5.0) == 8.0
    assert estimate_params_b("no-signals", 6.0) == 10.0  # 6.0/0.6 heuristic


def test_future_coder_family_resolves_by_pattern_and_size() -> None:
    """A family that doesn't exist yet must still resolve sensibly."""
    caps, tier, prov = resolve_model("qwen5-coder:40b", 24.0)
    assert "coding" in caps and prov == "inferred"

    caps, _, _ = resolve_model("qwen5-coder:8b", 5.0)
    assert "light_coding" in caps and "coding" not in caps

    caps, tier, _ = resolve_model("qwen5-coder:3b", 2.0)
    assert "coding" not in caps and "light_coding" not in caps
    assert "small" in tier


def test_unknown_family_no_signals_stays_conservative() -> None:
    caps, _, prov = resolve_model("zorbnak:12b", 8.0)
    assert caps == frozenset({"summarization", "classification"})
    assert prov == "default"


def test_metadata_family_counts_as_signal() -> None:
    """/api/show family feeds the same rules — a weird tag with a known
    coder family still resolves as a coder."""
    caps, _, prov = resolve_model(
        "mystery:latest", 12.0,
        metadata={"family": "deepseek-coder", "parameter_size": "16B"})
    assert "coding" in caps and prov == "inferred"


def test_vision_capability_surfaces_from_metadata() -> None:
    caps, _, _ = resolve_model("llava:13b", 8.0,
                               metadata={"capabilities": ["completion", "vision"]})
    assert "vision" in caps


# ── audition: measured overrides inference ───────────────────────────────────

def _canned_generate(code_ok: bool = True):
    """Fake local generation: perfect classification, a summary, and
    (optionally passing) code."""
    async def gen(prompt: str) -> str:
        if "Classify" in prompt:
            for kw, label in [("crashes", "bug"), ("dark mode", "feature"),
                              ("caching layer", "question"), ("README", "docs"),
                              ("utils.py", "refactor")]:
                if kw in prompt:
                    return label
            return "bug"
        if "Summarize" in prompt:
            return "The nightly scheduler dispatches due jobs with retries and timeouts."
        # coding task
        if code_ok:
            return (
                "```python\n"
                "def median_of(nums):\n"
                "    s = sorted(nums)\n"
                "    n = len(s)\n"
                "    mid = n // 2\n"
                "    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2\n"
                "\n"
                "def test_odd():\n    assert median_of([3, 1, 2]) == 2\n"
                "def test_even():\n    assert median_of([1, 2, 3, 4]) == 2.5\n"
                "def test_single():\n    assert median_of([7]) == 7\n"
                "```"
            )
        return "```python\ndef median_of(nums):\n    return 0\n\ndef test_odd():\n    assert median_of([3, 1, 2]) == 2\n```"
    return gen


async def _grade_8(doc: str, summary: str) -> int:
    return 8


@pytest.mark.asyncio
async def test_audition_measures_and_overrides_inference() -> None:
    await init_db()
    # "zorbnak" is an unknown family — inference alone says meta-tasks only.
    caps, _, prov = resolve_model("zorbnak:20b", 12.0)
    assert "coding" not in caps and prov == "default"

    with patch("backend.models_local.httpx.AsyncClient",
               side_effect=Exception("no ollama in tests")):
        measured = await audition_model(
            "zorbnak:20b", base_url="http://x:11434",
            generate=_canned_generate(code_ok=True), grader=_grade_8)

    assert measured["results"]["classification"]["passed"]
    assert measured["results"]["summarization"]["score"] == 8
    assert measured["results"]["coding"]["passed"]

    caps, tier, prov = resolve_model("zorbnak:20b", 12.0, measured=measured)
    assert prov == "measured"
    assert "coding" in caps and "summarization" in caps


@pytest.mark.asyncio
async def test_audition_failed_coding_grants_no_coding_cap() -> None:
    await init_db()
    with patch("backend.models_local.httpx.AsyncClient",
               side_effect=Exception("no ollama in tests")):
        measured = await audition_model(
            "zorbnak:20b", base_url="http://x:11434",
            generate=_canned_generate(code_ok=False), grader=_grade_8)
    assert not measured["results"]["coding"]["passed"]
    caps, _, prov = resolve_model("zorbnak:20b", 12.0, measured=measured)
    assert prov == "measured" and "coding" not in caps


# ── discovery: probe-once caching + new-digest nudge ─────────────────────────

_TAGS = {"models": [
    {"name": "qwen3-coder:30b", "size": int(18.6e9), "digest": "sha:v1"},
]}

_SHOW = {
    "details": {"family": "qwen3moe", "parameter_size": "30.5B",
                "quantization_level": "Q4_K_M"},
    "capabilities": ["completion", "tools"],
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _ProbeClient:
    """Fake Ollama with /api/tags, /api/ps and /api/show; counts show calls."""
    show_calls = 0

    def __init__(self, tags):
        self._tags = tags

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        if url.endswith("/api/ps"):
            return _FakeResponse({"models": []})
        return _FakeResponse(self._tags)

    async def post(self, url, json=None):
        type(self).show_calls += 1
        return _FakeResponse(_SHOW)


def _roomy() -> VRAMSnapshot:
    return VRAMSnapshot(gpus=[
        GPUInfo(index=0, name="RTX 3090", total_mb=24576, used_mb=0),
        GPUInfo(index=1, name="RTX 3090", total_mb=24576, used_mb=0),
    ])


@pytest.mark.asyncio
async def test_discovery_probes_once_per_digest_and_nudges() -> None:
    await init_db()
    _ProbeClient.show_calls = 0
    events: list = []

    async def capture(ev, **kw):
        events.append(ev)

    with patch("backend.models_local.httpx.AsyncClient",
               return_value=_ProbeClient(_TAGS)), \
         patch("backend.resources.vram_manager.snapshot",
               new=AsyncMock(return_value=_roomy())), \
         patch("backend.persistence.events.write_event", new=capture):
        first = await discover_local_models(base_url="http://x:11434",
                                            session_id="sess-nudge")
        again = await discover_local_models(base_url="http://x:11434",
                                            session_id="sess-nudge")

    # /api/show ran once — the second discovery hit the (model, digest) cache.
    assert _ProbeClient.show_calls == 1
    # Metadata reached resolution: parameter_size 30.5B + coder family.
    assert "coding" in first[0].capabilities
    assert first[0].provenance == "inferred" == again[0].provenance
    # MODEL_DISCOVERED emitted exactly once (first sight of this digest).
    discovered = [e for e in events if str(e.type) == "model/discovered"]
    assert len(discovered) == 1

    # The nudge lists it until an audition stores measured results.
    pending = await unauditioned_models()
    assert any(p["model"] == "qwen3-coder:30b" for p in pending)
    await record_measured("qwen3-coder:30b", "sha:v1",
                          {"capabilities": ["coding"], "results": {}})
    pending = await unauditioned_models()
    assert not any(p["model"] == "qwen3-coder:30b" and p["digest"] == "sha:v1"
                   for p in pending)


@pytest.mark.asyncio
async def test_new_digest_of_known_model_probes_and_nudges_again() -> None:
    await init_db()
    _ProbeClient.show_calls = 0
    tags_v2 = {"models": [
        {"name": "qwen3-coder:30b", "size": int(18.6e9), "digest": "sha:v2"},
    ]}
    events: list = []

    async def capture(ev, **kw):
        events.append(ev)

    with patch("backend.models_local.httpx.AsyncClient",
               return_value=_ProbeClient(tags_v2)), \
         patch("backend.resources.vram_manager.snapshot",
               new=AsyncMock(return_value=_roomy())), \
         patch("backend.persistence.events.write_event", new=capture):
        await discover_local_models(base_url="http://x:11434",
                                    session_id="sess-nudge2")

    assert _ProbeClient.show_calls == 1  # new digest → fresh probe
    assert any(str(e.type) == "model/discovered" for e in events)
    pending = await unauditioned_models()
    assert any(p["digest"] == "sha:v2" for p in pending)
