"""Part 6 — POST /api/lifecycle/shutdown: hermetic X-close teardown."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api import lifecycle_http
from backend.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_shutdown_kills_workers_and_schedules_exit(client: TestClient):
    with patch.object(lifecycle_http, "_kill_orphaned_workers",
                      return_value=["killed claude worker pid 123"]) as killer, \
         patch.object(lifecycle_http, "_schedule_exit") as scheduler:
        resp = client.post("/api/lifecycle/shutdown")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["workers"] == ["killed claude worker pid 123"]
    assert body["backend"] == "exiting"
    killer.assert_called_once()
    scheduler.assert_called_once()  # exit is scheduled AFTER the response


def test_shutdown_reports_when_nothing_to_kill(client: TestClient):
    with patch.object(lifecycle_http, "_kill_orphaned_workers",
                      return_value=["(no orphaned workers running)"]), \
         patch.object(lifecycle_http, "_schedule_exit"):
        resp = client.post("/api/lifecycle/shutdown")
    assert resp.json()["workers"] == ["(no orphaned workers running)"]


def test_kill_helper_prefers_the_stop_script(tmp_path):
    """The kill pattern lives in scripts/stop-hive-wsl.sh — the endpoint
    invokes it with --workers-only rather than duplicating the pattern."""
    script = tmp_path / "stop-hive-wsl.sh"
    script.write_text("#!/usr/bin/env bash\necho \"[WSL] killed 0 process(es).\"\n")
    with patch.object(lifecycle_http, "_STOP_SCRIPT", script):
        lines = lifecycle_http._kill_orphaned_workers()
    assert lines == ["[WSL] killed 0 process(es)."]


def test_kill_helper_falls_back_to_native_pattern(tmp_path):
    with patch.object(lifecycle_http, "_STOP_SCRIPT", tmp_path / "absent.sh"), \
         patch.object(lifecycle_http.subprocess, "run") as run:
        run.return_value.stdout = ""
        lines = lifecycle_http._kill_orphaned_workers()
    # pgrep ran with the narrow worker pattern; no pids → honest report.
    args = run.call_args[0][0]
    assert args[0] == "pgrep" and "stream-json" in args[2]
    assert lines == ["(no orphaned workers running)"]
