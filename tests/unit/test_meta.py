"""D8 — META agent: input assembly, report persistence, gated lesson-accept."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.meta.analyzer import assemble_inputs, run_meta
from backend.persistence.db import init_db
from backend.persistence.events import (
    create_agent,
    create_session,
    write_cost,
    write_event,
)
from backend.workers.base import EventType, HiveEvent


@pytest.fixture
async def db(tmp_path):
    p = tmp_path / "t.db"
    await init_db(p)
    return p


@pytest.mark.asyncio
async def test_assemble_inputs_on_seeded_data(db) -> None:
    await create_session("m1", db_path=db)
    await create_agent("a1", "m1", role="Builder", model="claude:sonnet",
                       worktree_path="/w", db_path=db)
    await write_cost("m1", "a1", 100, 200, 0.42, db_path=db)
    await write_event(HiveEvent(type=EventType.AGENT_ERROR, agent_id="a1",
                                session_id="m1", error="MCP preflight failed — token",
                                origin="infrastructure"), path=db)
    await write_event(HiveEvent(type=EventType.AGENT_ERROR, agent_id="a1",
                                session_id="m1", error="MCP preflight failed — token",
                                origin="infrastructure"), path=db)
    await write_event(HiveEvent(type=EventType.VALIDATION_FAILED, agent_id="a1",
                                session_id="m1",
                                raw_payload={"findings": ["claims x"]}), path=db)
    await write_event(HiveEvent(type=EventType.REVIEW_LLM, agent_id="reviewer",
                                session_id="m1",
                                raw_payload={"notes": ["fixed"]}), path=db)

    data = await assemble_inputs(db_path=db)
    assert data["scope"] == "global"
    top = data["failure_clusters"][0]
    assert top["origin"] == "infrastructure" and top["count"] == 2
    assert data["validation_failures"] == 1
    assert data["llm_review_interventions"] == 1
    assert data["cost_breakdown"][0]["cost"] == 0.42
    assert data["estimate_drift"]["samples"] == 0


@pytest.mark.asyncio
async def test_run_meta_persists_report_and_grounds_prompt(db, tmp_path) -> None:
    await create_session("m2", db_path=db)
    await create_agent("a2", "m2", role="Builder", model="claude:sonnet",
                       worktree_path="/w", db_path=db)

    captured: dict = {}

    async def fake_opus(prompt: str) -> str:
        captured["prompt"] = prompt
        return "# META Report\n## 1. What's working\neverything"

    project = tmp_path / "proj"
    project.mkdir()
    report, path = await run_meta(str(project), opus_caller=fake_opus, db_path=db)
    assert path == project / "META_REPORT.md"
    assert path.read_text().startswith("# META Report")
    assert "failure_clusters" in captured["prompt"]   # numbers fed to Opus
    assert "insufficient data" in captured["prompt"]  # honesty instruction


@pytest.mark.asyncio
async def test_run_meta_rejects_empty_report(db, tmp_path) -> None:
    async def empty(prompt: str) -> str:
        return "   "

    with pytest.raises(RuntimeError, match="empty report"):
        await run_meta(str(tmp_path), opus_caller=empty, db_path=db)


# ── gated lesson-accept ─────────────────────────────────────────────────────


def test_accept_lesson_goes_through_gate() -> None:
    body = {
        "title": "Check default branch", "description": "d",
        "content": "Verify the repo's actual default branch before diffing.",
        "trigger_context": "git merge/diff tasks",
        "evidence": "validator diagnosed wrong-branch comparison in session x",
    }

    class FakeDistiller:
        def __init__(self, score):
            self.score = score

        async def gate(self, draft, evidence):
            from backend.lessons.distiller import GateResult
            return GateResult(self.score, "test")

    # Low gate score → rejected, nothing saved.
    with TestClient(app) as client, \
         patch("backend.lessons.service._default_distiller",
               return_value=FakeDistiller(3)):
        resp = client.post("/api/meta/accept-lesson", json=body)
    assert resp.status_code == 422
    assert "gate" in resp.json()["detail"]

    # High gate score → saved through the same store as organic lessons.
    with TestClient(app) as client, \
         patch("backend.lessons.service._default_distiller",
               return_value=FakeDistiller(9)), \
         patch("backend.api.meta_http.save_lesson", create=True) as _:
        with patch("backend.lessons.store.embed") as fake_embed:
            import numpy as np
            fake_embed.return_value = np.ones(8, dtype=np.float32)
            resp = client.post("/api/meta/accept-lesson", json=body)
    assert resp.status_code == 200
    assert resp.json()["gate_score"] == 9
