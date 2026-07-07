"""Final close-out Part 1 — a worker that dies with ZERO emitted events must
always leave exactly one diagnostic AGENT_ERROR (exit code, stderr tail,
runtime) in the stream. The last relative of "claude exited 1 (no stderr)".
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.workers.base import EventType, WorkerConfig
from backend.workers.claude_cli import ClaudeCLIWorker


def _config() -> WorkerConfig:
    return WorkerConfig(agent_id="a-diag", session_id="s-diag",
                        model="claude:haiku", worktree_path="/tmp")


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
    proc.pid = 778
    proc.returncode = exit_code

    async def _wait():
        return exit_code

    proc.wait = _wait
    return proc


@pytest.mark.asyncio
async def test_nonzero_exit_with_zero_events_yields_one_diagnostic():
    """The spec case: exit != 0, nothing emitted → exactly one AGENT_ERROR
    carrying the exit code, tagged infrastructure (the agent never acted)."""
    proc = _proc([], b"boom: cli fell over\n", 3)
    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        events = [e async for e in ClaudeCLIWorker(oauth_token="t").run("x", _config())]

    errors = [e for e in events if e.type == EventType.AGENT_ERROR]
    assert len(errors) == 1 and len(events) == 1
    assert "exited 3" in errors[0].error
    assert "after" in errors[0].error and "s" in errors[0].error  # runtime
    assert "cli fell over" in errors[0].error
    assert errors[0].origin == "infrastructure"


@pytest.mark.asyncio
async def test_zero_exit_with_zero_events_is_an_error_not_success():
    """Exit 0 + no parseable output used to masquerade as a clean AGENT_END."""
    proc = _proc([], b"", 0)
    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        events = [e async for e in ClaudeCLIWorker(oauth_token="t").run("x", _config())]

    assert len(events) == 1
    assert events[0].type == EventType.AGENT_ERROR
    assert "exited 0 without emitting any events" in events[0].error
    assert events[0].origin == "infrastructure"


@pytest.mark.asyncio
async def test_nonzero_exit_with_events_keeps_unknown_origin():
    """With real events emitted, exit codes alone still can't assign fault
    (D0.2 semantics preserved)."""
    proc = _proc([{"type": "system", "subtype": "init"}], b"late crash\n", 1)
    with patch("asyncio.create_subprocess_exec", return_value=proc), \
         patch("os.getpgid", return_value=1):
        events = [e async for e in ClaudeCLIWorker(oauth_token="t").run("x", _config())]

    err = next(e for e in events if e.type == EventType.AGENT_ERROR)
    assert err.origin == "unknown"


@pytest.mark.asyncio
async def test_runner_synthesizes_diagnostic_when_worker_raises_before_yield():
    """The golden lessons-injection death: worker.run() raised before the
    first yield → the runner must persist exactly one diagnostic event."""
    from backend.orchestrator import graph as gmod
    from backend.orchestrator.nodes.spawner import SpawnedAgent

    written: list = []

    async def capture(ev, **kw):
        written.append(ev)

    class _ExplodingWorker:
        async def run(self, prompt, config):
            raise RuntimeError("resolved_claude_path blew up")
            yield  # pragma: no cover — makes this an async generator

    agent = SpawnedAgent(
        agent_id="w-diag", role="Writer", model="claude:haiku",
        worktree_path="/tmp/nonexistent-wt-diag", branch="b",
    )

    with patch.object(gmod, "ClaudeCLIWorker", return_value=_ExplodingWorker()), \
         patch.object(gmod, "write_event", new=capture), \
         patch.object(gmod, "_emit_to_ws", new=AsyncMockCompat()), \
         patch.object(gmod, "update_agent_status", new=AsyncMockCompat()), \
         patch.object(gmod, "_auto_commit_worktree", new=AsyncMockCompat()), \
         patch.object(gmod, "_ingest_guard_log", new=AsyncMockCompat()):
        result = await gmod._execute_worker(agent, "do the thing", "s-diag", 5)

    assert result["status"] == "failed"
    assert result["failure_origin"] == "infrastructure"
    diags = [e for e in written
             if str(e.type) == "agent/error" and "no events" in (e.error or "")]
    assert len(diags) == 1
    assert "resolved_claude_path blew up" in diags[0].error


class AsyncMockCompat:
    """Minimal awaitable-callable that swallows any signature."""
    def __call__(self, *a, **kw):
        async def _noop():
            return None
        return _noop()
