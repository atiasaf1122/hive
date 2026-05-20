"""Phase 6 HTTP endpoint tests — pipeline CRUD, webhook trigger, manual run.

Uses FastAPI TestClient with the store functions patched so tests don't touch
the user's real ~/.hive/hive.db.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


_FAKE_PIPELINE = {
    "id": "abc123abc123",
    "name": "Daily haiku",
    "task": "Write a haiku",
    "model": "claude:sonnet",
    "approval_mode": "full-auto",
    "schedule": "0 17 * * *",
    "webhook_token": "tok_" + "f" * 28,
    "enabled": 1,
    "created_at": "2026-05-18 12:00:00",
    "last_run_at": None,
    "next_run_at": None,
}


def test_list_pipelines_empty(client: TestClient) -> None:
    with patch("backend.api.pipelines_http.list_pipelines",
               new_callable=AsyncMock, return_value=[]):
        resp = client.get("/api/pipelines")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_pipelines_returns_items(client: TestClient) -> None:
    with patch("backend.api.pipelines_http.list_pipelines",
               new_callable=AsyncMock, return_value=[_FAKE_PIPELINE]):
        resp = client.get("/api/pipelines")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "abc123abc123"
    assert body[0]["name"] == "Daily haiku"
    assert body[0]["enabled"] is True
    assert body[0]["schedule"] == "0 17 * * *"


def test_create_pipeline_endpoint(client: TestClient) -> None:
    with patch("backend.api.pipelines_http.create_pipeline",
               new_callable=AsyncMock, return_value="abc123abc123"), \
         patch("backend.api.pipelines_http.get_pipeline",
               new_callable=AsyncMock, return_value=_FAKE_PIPELINE), \
         patch("backend.api.pipelines_http.sync_pipeline_schedule"):
        resp = client.post("/api/pipelines", json={
            "name": "Daily haiku",
            "task": "Write a haiku",
            "schedule": "0 17 * * *",
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "abc123abc123"
    assert body["webhook_token"].startswith("tok_")


def test_get_pipeline_endpoint_404(client: TestClient) -> None:
    with patch("backend.api.pipelines_http.get_pipeline",
               new_callable=AsyncMock, return_value=None):
        resp = client.get("/api/pipelines/missing")
    assert resp.status_code == 404


def test_get_pipeline_endpoint_ok(client: TestClient) -> None:
    with patch("backend.api.pipelines_http.get_pipeline",
               new_callable=AsyncMock, return_value=_FAKE_PIPELINE):
        resp = client.get("/api/pipelines/abc123abc123")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Daily haiku"


def test_update_pipeline_endpoint(client: TestClient) -> None:
    updated = {**_FAKE_PIPELINE, "name": "Renamed", "enabled": 0}
    with patch("backend.api.pipelines_http.get_pipeline",
               new_callable=AsyncMock, side_effect=[_FAKE_PIPELINE, updated]), \
         patch("backend.api.pipelines_http.update_pipeline", new_callable=AsyncMock), \
         patch("backend.api.pipelines_http.sync_pipeline_schedule"):
        resp = client.patch("/api/pipelines/abc123abc123",
                            json={"name": "Renamed", "enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["enabled"] is False


def test_delete_pipeline_endpoint(client: TestClient) -> None:
    with patch("backend.api.pipelines_http.get_pipeline",
               new_callable=AsyncMock, return_value=_FAKE_PIPELINE), \
         patch("backend.api.pipelines_http.delete_pipeline", new_callable=AsyncMock), \
         patch("backend.api.pipelines_http.sync_pipeline_schedule"):
        resp = client.delete("/api/pipelines/abc123abc123")
    assert resp.status_code == 204


def test_delete_pipeline_endpoint_404(client: TestClient) -> None:
    with patch("backend.api.pipelines_http.get_pipeline",
               new_callable=AsyncMock, return_value=None):
        resp = client.delete("/api/pipelines/missing")
    assert resp.status_code == 404


def test_list_runs_endpoint(client: TestClient) -> None:
    fake_run = {
        "id": "run000000001",
        "pipeline_id": "abc123abc123",
        "session_id": "sess1234",
        "triggered_by": "schedule",
        "status": "completed",
        "started_at": "2026-05-18 17:00:00",
        "ended_at": "2026-05-18 17:01:00",
    }
    with patch("backend.api.pipelines_http.list_pipeline_runs",
               new_callable=AsyncMock, return_value=[fake_run]):
        resp = client.get("/api/pipelines/abc123abc123/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "completed"
    assert body[0]["triggered_by"] == "schedule"


def test_webhook_trigger_unknown_token_404(client: TestClient) -> None:
    with patch("backend.api.pipelines_http.get_pipeline_by_webhook",
               new_callable=AsyncMock, return_value=None):
        resp = client.post("/api/pipelines/webhook/bad-token")
    assert resp.status_code == 404


def test_webhook_trigger_launches_session(client: TestClient) -> None:
    captured: dict = {}

    def fake_launch_session(**kwargs):
        captured.update(kwargs)
        import asyncio
        async def _noop() -> None:
            return None
        return asyncio.create_task(_noop())

    with patch("backend.api.pipelines_http.get_pipeline_by_webhook",
               new_callable=AsyncMock, return_value=_FAKE_PIPELINE), \
         patch("backend.api.pipelines_http.db_create_session", new_callable=AsyncMock), \
         patch("backend.api.pipelines_http.record_pipeline_run",
               new_callable=AsyncMock, return_value="run000000001"), \
         patch("backend.api.pipelines_http.finish_pipeline_run", new_callable=AsyncMock), \
         patch("backend.api.http.launch_session", fake_launch_session):
        resp = client.post("/api/pipelines/webhook/anytoken")

    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert body["run_id"] == "run000000001"
    assert captured["task"] == "Write a haiku"
    assert captured["model"] == "claude:sonnet"
    assert captured["approval_mode"] == "full-auto"


def test_run_now_endpoint(client: TestClient) -> None:
    captured: dict = {}

    def fake_launch_session(**kwargs):
        captured.update(kwargs)
        import asyncio
        async def _noop() -> None:
            return None
        return asyncio.create_task(_noop())

    with patch("backend.api.pipelines_http.get_pipeline",
               new_callable=AsyncMock, return_value=_FAKE_PIPELINE), \
         patch("backend.api.pipelines_http.db_create_session", new_callable=AsyncMock), \
         patch("backend.api.pipelines_http.record_pipeline_run",
               new_callable=AsyncMock, return_value="run000000002"), \
         patch("backend.api.pipelines_http.finish_pipeline_run", new_callable=AsyncMock), \
         patch("backend.api.http.launch_session", fake_launch_session):
        resp = client.post("/api/pipelines/abc123abc123/run")

    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert body["run_id"] == "run000000002"
    assert captured["task"] == "Write a haiku"
