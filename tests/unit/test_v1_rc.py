"""Coverage for the v1.0-rc1 hardening pass.

Buckets:
  - Per-session safety overrides (Section 6.4 / Item 1)
  - Skills hybrid search + rerank gate (Section 7 / Item 4)
  - NDJSON overflow recovery + WS replay ring buffer (Section 8 / Item 5)

These all hit pure-logic or in-memory paths — no claude CLI, no
WebSocket — so they run in well under a second.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import numpy as np
import pytest

from backend.api import event_bus
from backend.persistence.db import init_db
from backend.safety.hard_stops import DEFAULTS, HardStops
from backend.safety.overrides import (
    SafetyOverride,
    clear_override,
    effective_limits,
    load_override,
    merge,
    save_override,
)
from backend.skills import bm25 as bm25_mod
from backend.skills.bm25 import BM25, normalise, tokenize
from backend.skills.registry import (
    HybridHit,
    RerankResult,
    Skill,
    hybrid_search,
    maybe_rerank,
    should_use_rerank,
)
from backend.workers.base import EventType, WorkerConfig
from backend.workers.stream_parser import parse_stream


# ─────────────────────────────────────────────────────────────────────────────
# Item 1 — Per-session safety overrides
# ─────────────────────────────────────────────────────────────────────────────


def test_merge_none_fields_inherit_defaults():
    override = SafetyOverride()
    out = merge(DEFAULTS, override)
    assert out == DEFAULTS


def test_merge_replaces_only_set_fields():
    override = SafetyOverride(max_tokens_per_autonomous_run=1000)
    out = merge(DEFAULTS, override)
    assert out.max_tokens_per_autonomous_run == 1000
    # everything else is untouched
    assert out.max_session_duration_hours == DEFAULTS.max_session_duration_hours
    assert out.max_concurrent_agents == DEFAULTS.max_concurrent_agents
    assert out.max_same_file_edits == DEFAULTS.max_same_file_edits


def test_merge_can_loosen_or_tighten():
    # Tightening
    tight = merge(DEFAULTS, SafetyOverride(max_concurrent_agents=2))
    assert tight.max_concurrent_agents == 2
    # Loosening — caller responsibility, no policy here
    loose = merge(DEFAULTS, SafetyOverride(max_session_duration_hours=8.0))
    assert loose.max_session_duration_hours == 8.0


@pytest.mark.asyncio
async def test_save_and_load_override_roundtrip(tmp_path):
    db = tmp_path / "overrides.db"
    await init_db(db)

    sid = "sess-override-1"
    ov = SafetyOverride(
        max_tokens_per_autonomous_run=42_000,
        max_session_duration_hours=2.5,
        max_concurrent_agents=3,
        max_same_file_edits=7,
    )
    await save_override(sid, ov, db_path=db)

    loaded = await load_override(sid, db_path=db)
    assert loaded.max_tokens_per_autonomous_run == 42_000
    assert loaded.max_session_duration_hours == 2.5
    assert loaded.max_concurrent_agents == 3
    assert loaded.max_same_file_edits == 7


@pytest.mark.asyncio
async def test_load_override_missing_row_returns_empty(tmp_path):
    db = tmp_path / "overrides.db"
    await init_db(db)
    out = await load_override("nope", db_path=db)
    assert out == SafetyOverride()


@pytest.mark.asyncio
async def test_save_override_upserts(tmp_path):
    db = tmp_path / "overrides.db"
    await init_db(db)

    sid = "sess-upsert"
    await save_override(sid, SafetyOverride(max_concurrent_agents=2), db_path=db)
    await save_override(sid, SafetyOverride(max_concurrent_agents=5), db_path=db)

    loaded = await load_override(sid, db_path=db)
    assert loaded.max_concurrent_agents == 5


@pytest.mark.asyncio
async def test_clear_override(tmp_path):
    db = tmp_path / "overrides.db"
    await init_db(db)

    sid = "sess-clear"
    await save_override(sid, SafetyOverride(max_concurrent_agents=1), db_path=db)
    await clear_override(sid, db_path=db)

    loaded = await load_override(sid, db_path=db)
    assert loaded == SafetyOverride()


@pytest.mark.asyncio
async def test_effective_limits_merges_defaults(tmp_path):
    db = tmp_path / "overrides.db"
    await init_db(db)

    sid = "sess-effective"
    await save_override(
        sid,
        SafetyOverride(max_tokens_per_autonomous_run=99_999),
        db_path=db,
    )
    limits = await effective_limits(sid, db_path=db)
    assert isinstance(limits, HardStops)
    assert limits.max_tokens_per_autonomous_run == 99_999
    # untouched fields still come from DEFAULTS
    assert limits.max_concurrent_agents == DEFAULTS.max_concurrent_agents


# ─────────────────────────────────────────────────────────────────────────────
# Item 4 — Skills hybrid search + LLM rerank gate
# ─────────────────────────────────────────────────────────────────────────────


def test_tokenize_lowercases_and_keeps_hyphens():
    assert tokenize("HTTP-Server: Hello, world!") == ["http-server", "hello", "world"]


def test_tokenize_empty_input():
    assert tokenize("") == []
    assert tokenize(None) == []  # type: ignore[arg-type]


def test_bm25_zero_scores_on_empty_query():
    bm25 = BM25()
    bm25.fit(["alpha beta", "gamma delta"])
    assert bm25.score("") == [0.0, 0.0]


def test_bm25_ranks_exact_match_higher():
    bm25 = BM25()
    bm25.fit([
        "pytest fixtures and conftest",
        "django ORM queries",
        "rust async runtimes",
    ])
    scores = bm25.score("pytest fixtures")
    # The first doc must outrank the others by a clear margin.
    assert scores[0] > scores[1]
    assert scores[0] > scores[2]


def test_normalise_collapses_equal_scores_to_zero():
    assert normalise([1.0, 1.0, 1.0]) == [0.0, 0.0, 0.0]


def test_normalise_min_max():
    out = normalise([1.0, 3.0, 5.0])
    assert out == [0.0, 0.5, 1.0]


def test_should_use_rerank_triggers_on_many_agents():
    assert should_use_rerank(
        expected_agent_count=5,
        tech_stack_complete=True,
        ambiguous_query=False,
    )


def test_should_use_rerank_triggers_on_ambiguous_query():
    assert should_use_rerank(
        expected_agent_count=1,
        tech_stack_complete=True,
        ambiguous_query=True,
    )


def test_should_use_rerank_triggers_on_missing_stack():
    assert should_use_rerank(
        expected_agent_count=1,
        tech_stack_complete=False,
        ambiguous_query=False,
    )


def test_should_use_rerank_skips_obvious_case():
    assert not should_use_rerank(
        expected_agent_count=1,
        tech_stack_complete=True,
        ambiguous_query=False,
    )


def _fake_skill(idx: int, name: str) -> Skill:
    return Skill(
        id=name,
        name=name,
        description=f"Skill #{idx} — {name}",
        tags=["python"],
        path=f"/tmp/{name}/SKILL.md",
        instructions="",
        version=1,
    )


@pytest.mark.asyncio
async def test_maybe_rerank_returns_hits_unchanged_when_not_triggered():
    hits = [
        HybridHit(skill=_fake_skill(0, "alpha"), semantic=0.8,
                  keyword=0.5, tag_match=0.0, combined=0.7),
    ]
    result = await maybe_rerank(
        hits,
        query="pytest fixtures conftest pattern",  # 4 informative tokens
        tech_stack={"language": "python"},
        expected_agent_count=1,
    )
    assert isinstance(result, RerankResult)
    assert result.used_llm is False
    assert result.hits == hits
    assert "not triggered" in result.skipped_reason


@pytest.mark.asyncio
async def test_maybe_rerank_skips_without_caller_even_if_triggered():
    hits = [HybridHit(skill=_fake_skill(0, "alpha"), semantic=0.8,
                      keyword=0.5, tag_match=0.0, combined=0.7)]
    result = await maybe_rerank(
        hits,
        query="hi",  # ambiguous, < 4 informative tokens
        tech_stack=None,
        expected_agent_count=10,
    )
    assert result.used_llm is False
    assert result.skipped_reason == "not_wired"


@pytest.mark.asyncio
async def test_maybe_rerank_uses_haiku_when_caller_provided():
    hits = [
        HybridHit(skill=_fake_skill(0, "alpha"),
                  semantic=0.8, keyword=0.5, tag_match=0.0, combined=0.7),
        HybridHit(skill=_fake_skill(1, "bravo"),
                  semantic=0.7, keyword=0.4, tag_match=0.0, combined=0.6),
    ]

    async def fake_haiku(prompt: str) -> str:
        # Caller returns only the first ID — bravo must be filtered out.
        assert "alpha" in prompt and "bravo" in prompt
        return "alpha\n"

    result = await maybe_rerank(
        hits,
        query="hi",
        tech_stack=None,
        expected_agent_count=10,
        haiku_caller=fake_haiku,
    )
    assert result.used_llm is True
    assert [h.skill.id for h in result.hits] == ["alpha"]


@pytest.mark.asyncio
async def test_maybe_rerank_falls_back_on_haiku_error():
    hits = [HybridHit(skill=_fake_skill(0, "alpha"), semantic=0.8,
                      keyword=0.5, tag_match=0.0, combined=0.7)]

    async def boom(prompt: str) -> str:
        raise RuntimeError("haiku 429")

    result = await maybe_rerank(
        hits, query="hi", tech_stack=None,
        expected_agent_count=10, haiku_caller=boom,
    )
    assert result.used_llm is False
    assert "haiku_error" in result.skipped_reason


def _fake_embed(text: str) -> np.ndarray:
    """Deterministic embedding for tests — same input → same vector."""
    rng = np.random.default_rng(abs(hash(text)) % (2**32))
    vec = rng.random(384).astype(np.float32)
    return vec / np.linalg.norm(vec)


@pytest.mark.asyncio
async def test_hybrid_search_returns_empty_on_empty_db(tmp_path):
    db = tmp_path / "hybrid.db"
    await init_db(db)
    with patch("backend.skills.registry.embed", side_effect=_fake_embed):
        out = await hybrid_search("anything", db_path=db, threshold=0.0)
    assert out == []


@pytest.mark.asyncio
async def test_hybrid_search_blends_signals(tmp_path):
    """Three skills, one matches BOTH the query keyword AND the tag — it
    must rank first regardless of embedding noise."""
    db = tmp_path / "hybrid.db"
    await init_db(db)

    from backend.skills.registry import import_skill

    for slug, desc, tags in [
        ("pytest-guide", "pytest fixtures and conftest patterns", ["python", "testing"]),
        ("rust-async",   "rust tokio async runtimes",             ["rust"]),
        ("react-vite",   "react vite frontend tooling",           ["react", "frontend"]),
    ]:
        path = tmp_path / slug / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {slug}\ndescription: {desc}\ntags: {json.dumps(tags)}\nversion: 1\n---\n\nbody\n",
            encoding="utf-8",
        )
        with patch("backend.skills.registry.embed", side_effect=_fake_embed):
            await import_skill(path, db_path=db)

    with patch("backend.skills.registry.embed", side_effect=_fake_embed):
        hits = await hybrid_search(
            "pytest fixtures",
            tags=["python"],
            db_path=db,
            threshold=0.0,
        )

    assert hits, "tag filter should keep the python skill"
    assert hits[0].skill.id == "pytest-guide"
    # tag_match is Jaccard between {python} and skill tags
    assert hits[0].tag_match > 0


# ─────────────────────────────────────────────────────────────────────────────
# Item 5 — Streaming hardening: NDJSON overflow + WS resume ring buffer
# ─────────────────────────────────────────────────────────────────────────────


def _config() -> WorkerConfig:
    return WorkerConfig(
        agent_id="agent-v1rc",
        session_id="sess-v1rc",
        model="claude:sonnet",
        worktree_path="/tmp",
    )


@pytest.mark.asyncio
async def test_stream_parser_recovers_after_oversized_line():
    """A line bigger than MAX_BUFFER must be dropped without taking out
    the next, legitimate event."""
    from backend.workers import stream_parser as sp

    reader = asyncio.StreamReader()
    # Build something larger than MAX_BUFFER, no newlines inside.
    junk = b"x" * (sp.MAX_BUFFER + 1024)
    reader.feed_data(junk + b"\n")
    reader.feed_data(b'{"type": "system", "subtype": "init"}\n')
    reader.feed_eof()

    events = [e async for e in parse_stream(reader, _config())]
    types = [e.type for e in events]
    assert EventType.AGENT_START in types, types


@pytest.mark.asyncio
async def test_stream_parser_handles_oversized_with_no_newline():
    """If the buffer overflows AND there's no newline in sight, drop it
    all and resume on the next legit line."""
    from backend.workers import stream_parser as sp

    reader = asyncio.StreamReader()
    # Send oversized data with NO terminating newline at all.
    reader.feed_data(b"x" * (sp.MAX_BUFFER + 4096))
    # Then a clean event.
    reader.feed_data(b'\n{"type": "system", "subtype": "init"}\n')
    reader.feed_eof()

    events = [e async for e in parse_stream(reader, _config())]
    assert any(e.type == EventType.AGENT_START for e in events)


@pytest.mark.asyncio
async def test_stream_parser_drops_non_json_lines_silently():
    reader = asyncio.StreamReader()
    reader.feed_data(b"this is not JSON\n")
    reader.feed_data(b'{"type": "system", "subtype": "init"}\n')
    reader.feed_eof()
    events = [e async for e in parse_stream(reader, _config())]
    assert [e.type for e in events] == [EventType.AGENT_START]


@pytest.mark.asyncio
async def test_event_bus_assigns_monotonic_ids():
    sid = "sess-bus-1"
    event_bus.remove(sid)
    event_bus.get_or_create(sid)

    await event_bus.emit(sid, {"type": "a"})
    await event_bus.emit(sid, {"type": "b"})

    ring = event_bus.events_since(sid, 0)
    ids = [e["event_id"] for e in ring]
    assert len(ids) == 2
    assert ids[0] < ids[1]
    event_bus.remove(sid)


@pytest.mark.asyncio
async def test_event_bus_replay_filters_by_id():
    sid = "sess-bus-2"
    event_bus.remove(sid)
    event_bus.get_or_create(sid)

    await event_bus.emit(sid, {"type": "a"})
    cutoff = event_bus.latest_event_id(sid)
    await event_bus.emit(sid, {"type": "b"})
    await event_bus.emit(sid, {"type": "c"})

    missed = event_bus.events_since(sid, cutoff)
    assert [e["type"] for e in missed] == ["b", "c"]
    event_bus.remove(sid)


@pytest.mark.asyncio
async def test_event_bus_preserves_existing_event_id():
    sid = "sess-bus-3"
    event_bus.remove(sid)
    event_bus.get_or_create(sid)

    await event_bus.emit(sid, {"type": "a", "event_id": 99_999_999})
    assert event_bus.latest_event_id(sid) == 99_999_999
    event_bus.remove(sid)


@pytest.mark.asyncio
async def test_event_bus_ring_caps_at_max_replay():
    sid = "sess-bus-4"
    event_bus.remove(sid)
    event_bus.get_or_create(sid)

    cap = event_bus.MAX_REPLAY
    for i in range(cap + 50):
        await event_bus.emit(sid, {"type": "x", "i": i})

    ring = event_bus.events_since(sid, 0)
    assert len(ring) == cap
    # Oldest events should have rolled off — only the last `cap` remain.
    assert ring[0]["i"] >= 50
    event_bus.remove(sid)
