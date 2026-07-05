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
