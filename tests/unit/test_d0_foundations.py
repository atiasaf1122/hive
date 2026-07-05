"""D0 — stderr capture, failure-origin tagging, MCP doctor."""
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from backend.persistence.db import init_db
from backend.validation.trust import get_trust_score, record_completion
from backend.workers.base import EventType, WorkerConfig
from backend.workers.claude_cli import ClaudeCLIWorker


def _config() -> WorkerConfig:
    return WorkerConfig(agent_id="a-d0", session_id="s-d0",
                        model="claude:sonnet", worktree_path="/tmp")


def _proc(stdout_lines: list[dict], stderr: bytes, exit_code: int):
    out = asyncio.StreamReader()
    for line in stdout_lines:
        out.feed_data(json.dumps(line).encode() + b"\n")
    out.feed_eof()
    err = asyncio.StreamReader()
    if stderr:
        err.feed_data(stderr)
    err.feed_eof()
    proc = MagicMock()
    proc.stdout = out
    proc.stderr = err
    proc.pid = 777
    proc.returncode = exit_code

    async def _wait():
        return exit_code

    proc.wait = _wait
    return proc


# ── D0.1 stderr / stdout-tail capture ───────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_error_includes_stderr_tail():
    proc = _proc([{"type": "system", "subtype": "init"}],
                 b"FATAL: token expired\n", 1)
    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        events = [e async for e in ClaudeCLIWorker(oauth_token="t").run("x", _config())]

    err = next(e for e in events if e.type == EventType.AGENT_ERROR)
    assert "stderr tail" in err.error
    assert "token expired" in err.error
    assert err.origin == "unknown"


@pytest.mark.asyncio
async def test_failure_with_empty_stderr_includes_stdout_tail():
    """The C5 failure mode: exit 1, silent stderr. The event must carry the
    last NDJSON lines so it's diagnosable from the log alone."""
    proc = _proc([
        {"type": "system", "subtype": "init"},
        {"type": "result", "usage": {"input_tokens": 1, "output_tokens": 2}},
    ], b"", 1)
    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        events = [e async for e in ClaudeCLIWorker(oauth_token="t").run("x", _config())]

    err = next(e for e in events if e.type == EventType.AGENT_ERROR)
    assert "last stdout lines" in err.error
    assert '"result"' in err.error


# ── D0.2 origin-aware trust ─────────────────────────────────────────────────


@pytest.fixture
async def db(tmp_path):
    p = tmp_path / "t.db"
    await init_db(p)
    return p


@pytest.mark.asyncio
async def test_infrastructure_failure_does_not_touch_trust(db):
    await record_completion("m1", passed_validation=True, db_path=db)
    snap = await record_completion(
        "m1", passed_validation=False, origin="infrastructure", db_path=db)
    assert snap.failed_validations == 0
    assert snap.total_sessions == 1        # the infra failure added nothing
    assert snap.score == 1.0


@pytest.mark.asyncio
async def test_unknown_origin_failure_not_charged(db):
    out = await record_completion(
        "m2", passed_validation=False, origin="unknown", db_path=db)
    assert out is None                     # no row was ever created
    assert await get_trust_score("m2", db_path=db) is None


@pytest.mark.asyncio
async def test_agent_failure_still_counts(db):
    snap = await record_completion(
        "m3", passed_validation=False, origin="agent", db_path=db)
    assert snap.failed_validations == 1
    assert snap.score == 0.0


# ── D0.3 MCP doctor ─────────────────────────────────────────────────────────


_FAKE_SERVER = r"""
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("method") == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                          "result": {"serverInfo": {"name": "fake"}}}), flush=True)
"""


@pytest.mark.asyncio
async def test_doctor_passes_healthy_stdio_server():
    from backend.mcp.catalog import MCPServerSpec
    from backend.mcp.doctor import check_server, clear_cache

    clear_cache()
    spec = MCPServerSpec(id="fake", label="Fake", command=sys.executable,
                         args=["-c", _FAKE_SERVER])
    ok, detail = await check_server(spec, use_cache=False)
    assert ok, detail
    assert "fake" in detail


@pytest.mark.asyncio
async def test_doctor_fails_crashing_server_with_stderr():
    from backend.mcp.catalog import MCPServerSpec
    from backend.mcp.doctor import check_server, clear_cache

    clear_cache()
    spec = MCPServerSpec(
        id="crash", label="Crash", command=sys.executable,
        args=["-c", "import sys; sys.stderr.write('boom: bad flag'); sys.exit(1)"])
    ok, detail = await check_server(spec, use_cache=False)
    assert not ok
    assert "boom: bad flag" in detail


@pytest.mark.asyncio
async def test_doctor_caches_results():
    from backend.mcp.catalog import MCPServerSpec
    from backend.mcp.doctor import _CACHE, check_server, clear_cache

    clear_cache()
    spec = MCPServerSpec(id="fake2", label="Fake", command=sys.executable,
                         args=["-c", _FAKE_SERVER])
    await check_server(spec)
    assert len(_CACHE) == 1
    # Second call hits the cache (spawn would take measurable time; a
    # poisoned cache entry proves the hit).
    key = next(iter(_CACHE))
    _CACHE[key] = (False, "poisoned")
    ok, detail = await check_server(spec)
    assert detail == "poisoned"
