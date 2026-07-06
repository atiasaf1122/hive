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
def _no_real_model_calls():
    from unittest.mock import AsyncMock, patch

    with patch(
        "backend.orchestrator.task_router._haiku",
        new=AsyncMock(side_effect=RuntimeError("hermetic tests: no real model calls")),
    ), patch(
        "backend.orchestrator.task_router._ollama_generate",
        new=AsyncMock(side_effect=RuntimeError("hermetic tests: no real model calls")),
    ):
        yield
