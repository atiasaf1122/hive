"""Test-suite isolation: never touch the real ~/.hive.

This file is imported by pytest BEFORE any test module, and therefore before
any `backend.*` import resolves `HIVE_DIR`/`DB_PATH` (module-level constants
read from the environment at import time in backend/persistence/db.py). By
setting HIVE_DIR here, every DB, skills dir, session workspace, and worktree
root the suite creates lands in a throwaway temp directory.

Phase A dogfooding found ~250 junk sessions in the real hive.db written by
earlier test runs — this is the fix.
"""
from __future__ import annotations

import os
import tempfile

_TEST_HIVE_DIR = tempfile.mkdtemp(prefix="hive-test-")
os.environ["HIVE_DIR"] = _TEST_HIVE_DIR


def pytest_report_header(config):  # noqa: ARG001
    return f"HIVE_DIR isolated to {_TEST_HIVE_DIR}"


# ── hermetic model calls (E3 finding) ────────────────────────────────────────
# The task-shape classifier proved that a graph-node unit test can reach a
# REAL model (the router's Haiku fallback ran the actual claude CLI from
# pytest — slow, flaky, and it costs money). Block every model-call escape
# hatch by default; a test that wants one mocks it explicitly, and the
# router's fail-open design turns the block into the pre-E3 swarm path.
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_model_calls(request):
    from unittest.mock import AsyncMock, patch

    # Tests that exercise the detection functions themselves opt out of
    # the path patches (but never out of the model-call blocks).
    if "real_detection" in request.keywords:
        with patch(
            "backend.orchestrator.task_router._haiku",
            new=AsyncMock(side_effect=RuntimeError("hermetic tests: no real model calls")),
        ), patch(
            "backend.orchestrator.task_router._ollama_generate",
            new=AsyncMock(side_effect=RuntimeError("hermetic tests: no real model calls")),
        ):
            yield
        return

    with patch(
        "backend.orchestrator.task_router._haiku",
        new=AsyncMock(side_effect=RuntimeError("hermetic tests: no real model calls")),
    ), patch(
        "backend.orchestrator.task_router._ollama_generate",
        new=AsyncMock(side_effect=RuntimeError("hermetic tests: no real model calls")),
    ), patch(
        # Point Ollama discovery at an instantly-refused port. On WSL a
        # connect to an unbound localhost:11434 can HANG to the 4s
        # timeout, which added ~8s to every _execute_worker test once
        # the E4 summarizer started consulting the local pool. Tests
        # that exercise discovery pass base_url= or mock httpx anyway.
        "backend.detection.resolved_ollama_base",
        new=lambda: "http://127.0.0.1:9",
    ), patch(
        # Any un-mocked ClaudeCLIWorker spawn dies instantly instead of
        # running the REAL claude CLI (observed: the E4 summarizer path
        # silently spent ~7s + real API money per graph test). Worker
        # unit tests mock create_subprocess_exec and are unaffected.
        "backend.detection.resolved_claude_path",
        new=lambda: "/nonexistent/claude-blocked-by-hermetic-tests",
    ):
        yield
