"""Bugs found dogfooding the backend before the next snake-app try.

Three independent fixes covered here:

  - Bug 1: planner ran with ``cwd=/tmp`` regardless of workspace.
  - Bug 2: ``create_session_endpoint`` persisted ``path=''`` in the DB
           even when the caller supplied a real workspace.
  - Bug 3: a hung orchestrator subprocess produced *no* events to the
           UI; the watchdog now emits ``orchestrator_stall_hint``
           after ``HIVE_ORCH_STALL_WARN_S`` seconds.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.api import event_bus
from backend.api import http as http_mod
from backend.main import app
from backend.workers.base import EventType, HiveEvent, WorkerConfig


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    http_mod._pending_approvals.clear()
    http_mod._pending_inputs.clear()
    http_mod._running_tasks.clear()
    http_mod._message_queues.clear()


# ─── Bug 1: planner must NEVER run in the workspace ───────────────────────
#
# An earlier dogfood pass spawned the planner at cwd=project_path to give
# it read access to README / package.json when picking a team. That
# turned out to be unsafe: `claude --dangerously-skip-permissions` at
# the workspace cwd ignored the JSON-only instruction and built the
# project itself, leaving untracked files that then broke the Reviewer's
# merge. Until we can wire ``--allowed-tools`` to restrict the planner
# to read-only tools, the planner stays at /tmp regardless of the
# workspace.


@pytest.mark.asyncio
async def test_planner_sends_allowed_tools_whitelist() -> None:
    """Planner must restrict tool access to read-only. Without this Claude
    silently builds the project at cwd=/tmp and never spawns a worker."""
    from backend.orchestrator.nodes import planner as planner_mod

    captured: dict = {}

    async def stub_run(self, prompt, config):
        captured["allowed_tools"] = config.allowed_tools
        yield HiveEvent(
            type=EventType.AGENT_END,
            agent_id=config.agent_id, session_id=config.session_id,
        )

    with patch.object(planner_mod.ClaudeCLIWorker, "run", stub_run):
        await planner_mod.orchestrate(message="hi", session_id="s-tools")

    tools = captured["allowed_tools"]
    assert tools is not None, "planner must declare an allowed_tools list"
    # Read access — yes; Write/Edit/Bash — no.
    assert "Read" in tools
    assert "Write" not in tools
    assert "Edit" not in tools
    assert "Bash" not in tools


@pytest.mark.asyncio
async def test_claude_cli_passes_allowed_tools_to_subprocess(monkeypatch) -> None:
    """The whitelist actually lands as --allowed-tools on the CLI argv."""
    from backend.workers.base import WorkerConfig
    from backend.workers.claude_cli import ClaudeCLIWorker

    captured_argv: list[str] = []

    class FakeProc:
        returncode = 0
        pid = 99999
        stdout = None
        stderr = None

        async def wait(self):
            return 0

    async def fake_create(*args, **kwargs):
        captured_argv.extend(args)
        # Empty stream — parse_stream sees EOF immediately.
        import asyncio
        reader = asyncio.StreamReader()
        reader.feed_eof()
        FakeProc.stdout = reader
        FakeProc.stderr = reader
        return FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_create)
    monkeypatch.setattr("os.getpgid", lambda pid: 1)

    worker = ClaudeCLIWorker(oauth_token="t")
    config = WorkerConfig(
        agent_id="a", session_id="s", model="claude:sonnet",
        worktree_path="/tmp", allowed_tools=["Read", "Grep"],
    )
    _ = [ev async for ev in worker.run("x", config)]

    # argv should contain --allowed-tools "Read,Grep" in that order.
    assert "--allowed-tools" in captured_argv
    idx = captured_argv.index("--allowed-tools")
    assert captured_argv[idx + 1] == "Read,Grep"


@pytest.mark.asyncio
async def test_planner_streams_events_to_event_bus() -> None:
    """The planner mirrors text/tool events to the session bus so the
    UI can render an activity feed instead of a frozen 'thinking' pill."""
    from backend.api import event_bus
    from backend.orchestrator.nodes import planner as planner_mod

    sid = "s-stream"
    event_bus.remove(sid)
    event_bus.get_or_create(sid)

    async def stub_run(self, prompt, config):
        # Mimic a planner tool call + thinking text.
        yield HiveEvent(
            type=EventType.TOOL_USE, tool_name="Read",
            tool_input={"path": "/tmp/x"},
            agent_id=config.agent_id, session_id=sid,
        )
        yield HiveEvent(
            type=EventType.TEXT_DELTA, text="checking layout",
            agent_id=config.agent_id, session_id=sid,
        )
        yield HiveEvent(
            type=EventType.TEXT_DONE,
            text='{"response":"ok","team":[]}',
            agent_id=config.agent_id, session_id=sid,
        )
        yield HiveEvent(
            type=EventType.AGENT_END,
            agent_id=config.agent_id, session_id=sid,
        )

    with patch.object(planner_mod.ClaudeCLIWorker, "run", stub_run):
        await planner_mod.orchestrate(message="hi", session_id=sid)

    ring = event_bus.events_since(sid, 0)
    kinds = [e["kind"] for e in ring if e.get("type") == "planner_event"]
    assert "tool/use" in kinds, "tool call must be mirrored"
    assert "text/delta" in kinds, "partial text must be mirrored"
    event_bus.remove(sid)


@pytest.mark.asyncio
async def test_planner_cwd_is_project_path_when_valid(tmp_path) -> None:
    """The planner now runs at the user's project_path so Read/Glob/Grep
    can actually inspect the files. The previous design forced cwd=/tmp
    as a guardrail against the planner writing files, but allowed_tools
    restricts to read-only now so this is safe and necessary."""
    from backend.orchestrator.nodes import planner as planner_mod

    captured: dict = {}

    async def stub_run(self, prompt: str, config: WorkerConfig) -> AsyncIterator[HiveEvent]:
        captured["cwd"] = config.worktree_path
        captured["allowed_tools"] = config.allowed_tools
        yield HiveEvent(
            type=EventType.AGENT_END,
            agent_id=config.agent_id, session_id=config.session_id,
        )

    with patch.object(planner_mod.ClaudeCLIWorker, "run", stub_run):
        await planner_mod.orchestrate(
            message="hi", session_id="s-isolated",
            project_path=str(tmp_path),
        )
    assert captured["cwd"] == str(tmp_path)
    # The cwd switch is only safe because the planner is read-only.
    # If a future change drops the allowlist, the cwd guardrail must
    # come back — assert both halves of the invariant here.
    assert captured["allowed_tools"] == ["Read", "Glob", "Grep"]


@pytest.mark.asyncio
async def test_planner_falls_back_to_tmp_when_project_path_missing() -> None:
    from backend.orchestrator.nodes import planner as planner_mod

    captured: dict = {}

    async def stub_run(self, prompt: str, config: WorkerConfig) -> AsyncIterator[HiveEvent]:
        captured["cwd"] = config.worktree_path
        yield HiveEvent(
            type=EventType.AGENT_END,
            agent_id=config.agent_id, session_id=config.session_id,
        )

    with patch.object(planner_mod.ClaudeCLIWorker, "run", stub_run):
        await planner_mod.orchestrate(
            message="hi", session_id="s-no-path",
            project_path="/nonexistent/path/that/should/not/exist",
        )
    assert captured["cwd"] == "/tmp"


# ─── Bug 2: project_path must be persisted to the sessions table ──────────


@pytest.fixture(autouse=True)
def _stub_launch():
    """Keep the session runner a no-op for these tests."""
    with patch("backend.api.http.launch_session") as mocked:
        mocked.return_value = None
        yield mocked


def test_create_session_persists_project_path(tmp_path, client: TestClient) -> None:
    """Sessions previously stored path='' because the call site forgot to
    forward project_path to db_create_session."""
    resp = client.post("/api/sessions", json={
        "task": "do thing", "project_path": str(tmp_path),
        "approval_mode": "manual",
    })
    assert resp.status_code == 200
    sid = resp.json()["session_id"]

    import sqlite3

    from backend.persistence.db import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT path FROM sessions WHERE id=?", (sid,),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["path"] == str(tmp_path)


def test_create_session_default_path_is_recorded(client: TestClient) -> None:
    """No supplied path → backend builds ~/.hive/sessions/<id>/ and records it."""
    resp = client.post("/api/sessions", json={"task": "x"})
    assert resp.status_code == 200
    sid = resp.json()["session_id"]

    import sqlite3

    from backend.persistence.db import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT path FROM sessions WHERE id=?", (sid,),
    ).fetchone()
    conn.close()
    assert row is not None
    # Whatever path the backend picked, it must be non-empty.
    assert row["path"].strip() != ""
    assert sid in row["path"]


@pytest.mark.asyncio
async def test_run_session_persists_workspace_path_and_approval_mode(tmp_path) -> None:
    """graph.run_session() previously omitted path= and approval_mode=, so
    rows it created stored '' / 'full-auto' regardless of what the caller
    intended (incomplete fix from commit 1b058dd)."""
    from backend.orchestrator.graph import SessionInterrupt, run_session
    from backend.persistence.db import init_db

    db = tmp_path / "test.db"
    await init_db(db)

    async def fake_orchestrator(state):  # type: ignore[no-untyped-def]
        return {"team_composition": {"team": [], "confidence": 1.0}}

    with patch("backend.orchestrator.graph.orchestrator_node", fake_orchestrator):
        result = await run_session(
            session_id="sess-rs",
            agent_id="a1",
            task="hello",
            model="claude:sonnet",
            worktree_path=str(tmp_path),
            approval_mode="manual",
            db_path=db,
        )

    # The graph parks; we only care about the persisted row.
    assert isinstance(result, SessionInterrupt)

    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT path, approval_mode FROM sessions WHERE id=?",
                       ("sess-rs",)).fetchone()
    conn.close()
    assert row is not None
    assert row["path"] == str(tmp_path)
    assert row["approval_mode"] == "manual"


@pytest.mark.asyncio
async def test_pipelines_run_cli_persists_workspace_path(monkeypatch, tmp_path) -> None:
    """cli/hive.py pipelines-run direct path previously omitted path=, so
    its sessions weren't recoverable. The fix mirrors the HTTP/scheduler
    call sites: pass the computed workspace string explicitly."""
    captured: dict = {}

    async def fake_create_session(session_id, **kw):
        captured.update({"session_id": session_id, **kw})

    async def fake_record_run(pipeline_id, session_id, triggered_by):
        return "run-1"

    async def fake_finish_run(run_id, status):
        return None

    async def fake_run_session(**kw):
        return {"status": "completed"}

    pipeline = {
        "id": "pl-1",
        "task": "do thing",
        "model": "claude:sonnet",
        "approval_mode": "manual",
    }

    async def fake_init_db():
        return None

    async def fake_get_pipeline(_pid):
        return pipeline

    from cli import hive as cli_mod
    monkeypatch.setattr(
        "backend.persistence.db.init_db", fake_init_db
    )
    monkeypatch.setattr(
        "backend.pipelines.store.get_pipeline", fake_get_pipeline
    )
    monkeypatch.setattr(
        "backend.persistence.events.create_session", fake_create_session
    )
    monkeypatch.setattr(
        "backend.pipelines.store.record_pipeline_run", fake_record_run
    )
    monkeypatch.setattr(
        "backend.pipelines.store.finish_pipeline_run", fake_finish_run
    )
    monkeypatch.setattr(
        "backend.orchestrator.graph.run_session", fake_run_session
    )
    monkeypatch.setattr(cli_mod, "_print_result", lambda *_a, **_kw: None)

    await cli_mod._pipelines_run_async("pl-1")

    assert "path" in captured, "create_session was called without path="
    assert captured["path"].strip() != ""
    assert captured["session_id"] in captured["path"]
    assert captured["approval_mode"] == "manual"


# ─── Bug 3: orchestrator stall watchdog ───────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_stall_hint_fires_when_silent(monkeypatch) -> None:
    """If the orchestrator hasn't streamed anything by the deadline, the
    runner must emit an ``orchestrator_stall_hint`` event so the UI can
    surface "still thinking…" rather than a silent void."""
    sid = "s-stall"
    event_bus.remove(sid)
    event_bus.get_or_create(sid)

    # Tighten the deadline so the test stays fast.
    monkeypatch.setenv("HIVE_ORCH_STALL_WARN_S", "0.05")

    # Stub run_session so it never emits and never returns; we only want
    # to observe the watchdog firing.
    async def never_returns(**kwargs):
        await asyncio.sleep(5)
        return None

    monkeypatch.setattr("backend.api.http.run_session", never_returns)

    runner = asyncio.create_task(http_mod._session_runner(
        session_id=sid, task="t", model="claude:sonnet",
        approval_mode="manual", project_path="/tmp", max_turns=1,
    ))
    try:
        # Wait past the watchdog deadline.
        await asyncio.sleep(0.4)
        hints = [
            e for e in event_bus.events_since(sid, 0)
            if e.get("type") == "orchestrator_stall_hint"
        ]
        assert hints, "watchdog must emit at least one stall hint"
        assert "claude --version" in hints[0]["hint"]
    finally:
        runner.cancel()
        try:
            await runner
        except (asyncio.CancelledError, Exception):
            pass
        event_bus.remove(sid)


@pytest.mark.asyncio
async def test_orchestrator_text_not_duplicated_with_text_done() -> None:
    """Regression: when both `stream_event` deltas AND the consolidated
    `assistant` message land, the orchestrator returned the same text
    twice (visible in chat as a duplicated paragraph)."""
    from backend.orchestrator.nodes import planner as planner_mod

    async def stub_run(self, prompt, config):
        # Partial deltas + the consolidated assistant message — both
        # contain the same logical text.
        for chunk in ('{"response":"', 'hi","team":[', ']}'):
            yield HiveEvent(
                type=EventType.TEXT_DELTA, text=chunk,
                agent_id=config.agent_id, session_id=config.session_id,
            )
        yield HiveEvent(
            type=EventType.TEXT_DONE, text='{"response":"hi","team":[]}',
            agent_id=config.agent_id, session_id=config.session_id,
        )
        yield HiveEvent(
            type=EventType.AGENT_END,
            agent_id=config.agent_id, session_id=config.session_id,
        )

    with patch.object(planner_mod.ClaudeCLIWorker, "run", stub_run):
        decision = await planner_mod.orchestrate(
            message="hi", session_id="dup-check", project_path="/tmp",
        )
    # Without the TEXT_DONE fix the response would be the JSON twice
    # concatenated — and json.loads would reject the result, dropping
    # back to the fallback team. The fix means we see exactly one
    # parsed decision.
    assert decision.response == "hi"


@pytest.mark.asyncio
async def test_stall_hint_silent_when_orchestrator_streams(monkeypatch) -> None:
    """When the orchestrator emits an event before the deadline, the
    watchdog must stand down."""
    sid = "s-quiet"
    event_bus.remove(sid)
    event_bus.get_or_create(sid)

    monkeypatch.setenv("HIVE_ORCH_STALL_WARN_S", "0.1")

    async def streams_immediately(**kwargs):
        # Mimic what orchestrator_node does on its first call.
        await event_bus.emit(kwargs["session_id"], {"type": "orchestrator_thinking"})
        await asyncio.sleep(0.3)
        return None

    monkeypatch.setattr("backend.api.http.run_session", streams_immediately)

    runner = asyncio.create_task(http_mod._session_runner(
        session_id=sid, task="t", model="claude:sonnet",
        approval_mode="manual", project_path="/tmp", max_turns=1,
    ))
    try:
        await asyncio.sleep(0.4)
        hints = [
            e for e in event_bus.events_since(sid, 0)
            if e.get("type") == "orchestrator_stall_hint"
        ]
        assert hints == [], "watchdog must stay silent once events flow"
    finally:
        runner.cancel()
        try:
            await runner
        except (asyncio.CancelledError, Exception):
            pass
        event_bus.remove(sid)
