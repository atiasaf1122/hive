"""F3 — salvage review: failed-with-commits triggers, empty/trivial skip,
merge verdict flows through merge, discard logs reasoning."""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from backend.orchestrator.nodes.reviewer import salvage_failed_agents
from backend.orchestrator.nodes.spawner import SpawnedAgent, SpawnPlan
from backend.persistence.db import init_db
from backend.persistence.events import create_session
from backend.workers.base import EventType, HiveEvent


async def _git(*args, cwd):
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode())
    return out.decode()


async def _repo_with_failed_branch(tmp_path, session: str, *, commit_lines: int) -> str:
    """Init a repo on main, then a hive/ branch with a committed change."""
    proj = tmp_path / "proj"
    proj.mkdir()
    await _git("init", "-b", "main", cwd=proj)
    await _git("config", "user.email", "t@t", cwd=proj)
    await _git("config", "user.name", "t", cwd=proj)
    (proj / "app.py").write_text("x = 1\n")
    await _git("add", "-A", cwd=proj)
    await _git("commit", "-qm", "init", cwd=proj)
    # the failed agent's branch
    await _git("branch", f"hive/{session}/tester-0", cwd=proj)
    await _git("worktree", "add", "-q", str(tmp_path / "wt"),
               f"hive/{session}/tester-0", cwd=proj)
    wt = tmp_path / "wt"
    body = "\n".join(f"line{i}" for i in range(commit_lines))
    (wt / "test_app.py").write_text(body + "\n")
    await _git("add", "-A", cwd=wt)
    await _git("commit", "-qm", "wrote tests", cwd=wt)
    await _git("worktree", "remove", "--force", str(wt), cwd=proj)   # mimic cleanup
    return str(proj)


def _plan(proj: str, session: str) -> SpawnPlan:
    return SpawnPlan(
        session_id=session, project_path=proj,
        active_agents=[SpawnedAgent(
            agent_id="tester-0", role="Tester", model="claude:sonnet",
            worktree_path=f"{proj}/wt")])


_FAILED = {"tester-0": {"status": "failed", "error": "claude exited 1",
                        "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}


@pytest.fixture
async def db():
    # Salvage writes events/cost via the DEFAULT (conftest-isolated) DB —
    # it takes no db_path, so we init that one, not a separate file. A
    # unique session per test avoids cross-test event bleed.
    from backend.persistence.db import DB_PATH as default_db
    await init_db(default_db)
    session = f"salv{uuid.uuid4().hex[:6]}"
    await create_session(session, db_path=default_db)
    return default_db, session


async def _events(session, db):
    from backend.persistence.events import get_session_events
    evs = await get_session_events(session, path=db)
    return [e for e in evs if e["type"] == str(EventType.SALVAGE_REVIEW)]


def _fake_worker(verdict_json: str):
    class _W:
        def __init__(self, *a, **kw): ...

        async def run(self, prompt, config):
            yield HiveEvent(type=EventType.COST, agent_id=config.agent_id,
                            session_id=config.session_id,
                            input_tokens=100, output_tokens=20, cost_usd=0.15)
            yield HiveEvent(type=EventType.TEXT_DONE, agent_id=config.agent_id,
                            session_id=config.session_id, text=verdict_json)

        async def kill(self, agent_id): ...
    return _W


@pytest.mark.asyncio
async def test_merge_verdict_merges_the_branch(tmp_path, db) -> None:
    default_db, session = db
    proj = await _repo_with_failed_branch(tmp_path, session, commit_lines=12)

    with patch("backend.workers.claude_cli.ClaudeCLIWorker",
               _fake_worker('{"action": "merge", "reason": "tests look correct"}')):
        verdicts = await salvage_failed_agents(_plan(proj, session), _FAILED, session)

    assert len(verdicts) == 1 and verdicts[0].action == "merge"
    # the file the failed agent wrote is now on main
    log = await _git("log", "--oneline", "main", cwd=proj)
    assert "wrote tests" in log
    evs = await _events(session, default_db)
    assert evs and evs[0]["payload"]["raw_payload"]["action"] == "merge"


@pytest.mark.asyncio
async def test_discard_verdict_logs_reasoning_no_merge(tmp_path, db) -> None:
    default_db, session = db
    proj = await _repo_with_failed_branch(tmp_path, session, commit_lines=12)

    with patch("backend.workers.claude_cli.ClaudeCLIWorker",
               _fake_worker('{"action": "discard", "reason": "tests are broken"}')):
        verdicts = await salvage_failed_agents(_plan(proj, session), _FAILED, session)

    assert verdicts[0].action == "discard"
    log = await _git("log", "--oneline", "main", cwd=proj)
    assert "wrote tests" not in log       # NOT merged
    evs = await _events(session, default_db)
    assert "broken" in evs[0]["payload"]["raw_payload"]["reasoning"]


@pytest.mark.asyncio
async def test_trivial_branch_skips_opus(tmp_path, db) -> None:
    default_db, session = db
    proj = await _repo_with_failed_branch(tmp_path, session, commit_lines=1)  # 1 line < 5

    worker = _fake_worker('{"action": "merge"}')
    with patch("backend.workers.claude_cli.ClaudeCLIWorker", worker) as _:
        verdicts = await salvage_failed_agents(_plan(proj, session), _FAILED, session)

    assert verdicts == []                 # never paid for Opus
    assert await _events(session, default_db) == []


@pytest.mark.asyncio
async def test_succeeded_agent_not_salvaged(tmp_path, db) -> None:
    default_db, session = db
    proj = await _repo_with_failed_branch(tmp_path, session, commit_lines=12)
    completed = {"tester-0": {"status": "completed", "error": None,
                              "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}

    with patch("backend.workers.claude_cli.ClaudeCLIWorker",
               _fake_worker('{"action": "merge"}')):
        verdicts = await salvage_failed_agents(_plan(proj, session), completed, session)
    assert verdicts == []
