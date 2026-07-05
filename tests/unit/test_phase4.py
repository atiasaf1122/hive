"""Phase 4 tests: skills registry, embedder, injector, and graph injection."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from backend.persistence.db import init_db
from backend.skills.embedder import cosine_similarity, deserialize, serialize
from backend.skills.injector import build_skill_context
from backend.skills.registry import Skill, _slugify, parse_skill_file


# ── embedder unit tests ───────────────────────────────────────────────────────

def test_serialize_deserialize_roundtrip():
    vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    assert np.allclose(deserialize(serialize(vec)), vec)


def test_cosine_similarity_identical():
    vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert cosine_similarity(vec, vec) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_slugify():
    assert _slugify("Python Testing") == "python-testing"
    assert _slugify("my_skill!@#") == "my-skill"  # trailing dashes stripped


# ── parse_skill_file ──────────────────────────────────────────────────────────

def test_parse_skill_file_valid(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        '---\nname: pytest-guide\ndescription: Write pytest tests\ntags: ["python","testing"]\nversion: 1\n---\n\n## Instructions\nUse fixtures.',
        encoding="utf-8",
    )
    fm, body = parse_skill_file(skill_md)
    assert fm["name"] == "pytest-guide"
    assert fm["description"] == "Write pytest tests"
    assert "python" in fm["tags"]
    assert "Use fixtures" in body


def test_parse_skill_file_missing_frontmatter(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text("Just markdown, no frontmatter.", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        parse_skill_file(skill_md)


# ── registry CRUD (with mocked embed) ────────────────────────────────────────

def _fake_embed(text: str) -> np.ndarray:
    """Returns a deterministic fake embedding based on string length."""
    rng = np.random.default_rng(len(text))
    vec = rng.random(384).astype(np.float32)
    return vec / np.linalg.norm(vec)


@pytest.mark.asyncio
async def test_import_and_list_skill(tmp_path):
    db = tmp_path / "test.db"
    await init_db(db)

    skill_md = tmp_path / "pytest-guide" / "SKILL.md"
    skill_md.parent.mkdir()
    skill_md.write_text(
        '---\nname: pytest-guide\ndescription: Write pytest tests for Python\ntags: ["python","testing"]\nversion: 1\n---\n\n## Instructions\nUse fixtures.',
        encoding="utf-8",
    )

    with patch("backend.skills.registry.embed", side_effect=_fake_embed):
        from backend.skills.registry import import_skill, list_skills
        skill = await import_skill(skill_md, db_path=db)

    assert skill.id == "pytest-guide"
    assert skill.name == "pytest-guide"
    assert "python" in skill.tags

    with patch("backend.skills.registry.embed", side_effect=_fake_embed):
        skills = await list_skills(db_path=db)

    assert len(skills) == 1
    assert skills[0].id == "pytest-guide"
    assert "Use fixtures" in skills[0].instructions


@pytest.mark.asyncio
async def test_import_missing_description_raises(tmp_path):
    db = tmp_path / "test.db"
    await init_db(db)

    skill_md = tmp_path / "bad" / "SKILL.md"
    skill_md.parent.mkdir()
    skill_md.write_text(
        "---\nname: bad-skill\n---\n\n## Instructions\nNo description.",
        encoding="utf-8",
    )

    from backend.skills.registry import import_skill
    with pytest.raises(ValueError, match="description"):
        await import_skill(skill_md, db_path=db)


@pytest.mark.asyncio
async def test_create_skill_file(tmp_path):
    from backend.skills.registry import create_skill_file
    path = await create_skill_file(
        name="Git Workflow",
        description="Manage git branches",
        tags=["git"],
        skills_root=tmp_path,
    )
    assert path.exists()
    content = path.read_text()
    assert "Git Workflow" in content
    assert "Manage git branches" in content


# ── semantic search ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_returns_relevant_skill(tmp_path):
    """A skill with similar description to the query should rank above one that doesn't."""
    db = tmp_path / "test.db"
    await init_db(db)

    # Create two skills with very different descriptions
    def _create_md(directory, name, description, body):
        p = tmp_path / directory / "SKILL.md"
        p.parent.mkdir()
        p.write_text(
            f"---\nname: {name}\ndescription: {description}\ntags: []\nversion: 1\n---\n\n{body}",
            encoding="utf-8",
        )
        return p

    md_tests = _create_md("pytest", "pytest-guide", "write pytest tests for Python code", "Use fixtures and parametrize.")
    md_git   = _create_md("git",    "git-workflow",  "manage git branches and commits",    "Use feature branches.")

    # Use real cosine similarity but fake embeddings: make pytest-guide closer to the query
    query = "write unit tests"
    query_vec = np.array([1.0, 0.0], dtype=np.float32)
    pytest_vec = np.array([0.9, 0.1], dtype=np.float32)  # similar to query
    git_vec    = np.array([0.0, 1.0], dtype=np.float32)  # orthogonal to query

    # Map text to pre-set vectors
    _embeddings = {
        "write pytest tests for Python code": pytest_vec,
        "manage git branches and commits": git_vec,
        query: query_vec,
    }
    # Normalize
    for k in _embeddings:
        _embeddings[k] = _embeddings[k] / np.linalg.norm(_embeddings[k])

    def _controlled_embed(text: str) -> np.ndarray:
        # return known vec for description/query, fallback to fake
        base = _embeddings.get(text)
        if base is not None:
            return base.astype(np.float32)
        return _fake_embed(text)

    with patch("backend.skills.registry.embed", side_effect=_controlled_embed):
        from backend.skills.registry import import_skill, search
        await import_skill(md_tests, db_path=db)
        await import_skill(md_git, db_path=db)
        results = await search(query, top_k=3, threshold=0.0, db_path=db)

    assert len(results) >= 1
    assert results[0].id == "pytest-guide"  # must rank first


@pytest.mark.asyncio
async def test_search_empty_db_returns_empty(tmp_path):
    db = tmp_path / "test.db"
    await init_db(db)

    with patch("backend.skills.registry.embed", side_effect=_fake_embed):
        from backend.skills.registry import search
        results = await search("anything", db_path=db)

    assert results == []


# ── injector ─────────────────────────────────────────────────────────────────

def test_build_skill_context_empty():
    assert build_skill_context([]) == ""


def test_build_skill_context_single():
    skill = Skill(
        id="pytest-guide", name="pytest-guide",
        description="Write pytest tests",
        tags=["python"], path="/tmp/SKILL.md",
        instructions="## Instructions\nUse fixtures.",
    )
    ctx = build_skill_context([skill])
    assert "## Relevant Skills" in ctx
    assert "pytest-guide" in ctx
    assert "Use fixtures" in ctx


def test_build_skill_context_multiple():
    skills = [
        Skill(id="s1", name="Skill One", description="desc one", tags=[], path="", instructions="Body one."),
        Skill(id="s2", name="Skill Two", description="desc two", tags=[], path="", instructions="Body two."),
    ]
    ctx = build_skill_context(skills)
    assert "Skill One" in ctx
    assert "Skill Two" in ctx
    assert ctx.index("Skill One") < ctx.index("Skill Two")


# ── graph injection ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_worker_injects_skill_context(tmp_path, monkeypatch):
    """_execute_worker should pass skill context as system_prompt to the worker."""
    from backend.orchestrator.graph import _execute_worker
    from backend.orchestrator.nodes.spawner import SpawnedAgent
    from backend.persistence.db import init_db
    from backend.persistence.events import create_session
    from backend.workers.base import EventType, HiveEvent

    db = tmp_path / "test.db"
    await init_db(db)
    await create_session("sess-skill", db_path=db)

    captured_config = {}

    async def _mock_run(prompt, config):
        # B3 note: the same mocked worker also serves the post-run Haiku
        # summarizer call — only the FIRST call is the actual agent run,
        # so capture once (the summarizer's config has no skill context).
        captured_config.setdefault("system_prompt", config.system_prompt)
        yield HiveEvent(type=EventType.AGENT_START, agent_id="ag1", session_id="sess-skill")
        yield HiveEvent(type=EventType.TEXT_DELTA, agent_id="ag1", session_id="sess-skill", text="done")
        yield HiveEvent(type=EventType.AGENT_END, agent_id="ag1", session_id="sess-skill")

    mock_worker = MagicMock()
    mock_worker.run = _mock_run

    skill_ctx = "## Relevant Skills\n### pytest-guide\n_Write tests_\n"

    import backend.orchestrator.graph as gmod
    monkeypatch.setattr(gmod, "write_event", lambda e: _noop())
    monkeypatch.setattr(gmod, "write_cost", lambda *a, **k: _noop())
    monkeypatch.setattr(gmod, "update_agent_status", lambda *a, **k: _noop())
    monkeypatch.setattr("backend.orchestrator.graph.ClaudeCLIWorker", lambda: mock_worker)

    async def fake_search(query, top_k=3, threshold=0.3, db_path=None):
        return [Skill(id="pytest-guide", name="pytest-guide", description="Write tests",
                      tags=[], path="", instructions="Use fixtures.")]

    def fake_build_context(skills):
        return skill_ctx

    monkeypatch.setattr("backend.orchestrator.graph.search_skills", fake_search)
    monkeypatch.setattr("backend.orchestrator.graph.build_skill_context", fake_build_context)

    agent = SpawnedAgent(agent_id="ag1", role="Builder", model="claude:sonnet", worktree_path=str(tmp_path))
    result = await _execute_worker(agent, "write tests", "sess-skill", 5)

    assert result["status"] == "completed"
    assert captured_config.get("system_prompt") == skill_ctx


async def _noop(*args, **kwargs):
    pass
