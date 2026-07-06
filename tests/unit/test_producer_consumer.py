"""F4 — producer/consumer runtime net: parse-time resequence (PLAN_ADJUSTED)
and spawn-time fail-fast on a missing declared input."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.orchestrator.nodes.planner import _parse_composition_dict
from backend.orchestrator.nodes.spawner import SpawnedAgent


def _plan(team):
    return {"response": "ok", "team": team, "confidence": 0.9, "rationale": "r"}


# ── F4.2: parse-time resequence + adjustments ────────────────────────────────


def test_produce_consume_same_wave_gets_resequenced() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "write it",
         "files_hint": ["index.html"]},
        {"role": "Tester", "model": "claude:sonnet", "subtask": "verify it",
         "files_hint": ["index.html"]},   # consumes what Builder produces
    ]))
    roles = {m.role: m for m in comp.team}
    assert roles["Builder"].wave == 0
    assert roles["Tester"].wave == 1              # moved to the next wave
    assert comp.plan_adjustments
    adj = comp.plan_adjustments[0]
    assert adj["kind"] == "resequenced"
    assert adj["consumer"] == "Tester" and adj["producer"] == "Builder"
    assert "index.html" in adj["files"]


def test_disjoint_files_no_adjustment() -> None:
    comp = _parse_composition_dict(_plan([
        {"role": "Builder", "model": "claude:sonnet", "subtask": "a",
         "files_hint": ["app.py"]},
        {"role": "Tester", "model": "claude:sonnet", "subtask": "b",
         "files_hint": ["test_app.py"]},
    ]))
    assert all(m.wave == 0 for m in comp.team)
    assert comp.plan_adjustments == []


# ── F4.1: fail-fast on a missing declared input ──────────────────────────────


async def _git(*args, cwd):
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(err.decode())
    return out.decode()


async def _repo(tmp_path, *, producer_committed: bool):
    proj = tmp_path / "proj"
    proj.mkdir()
    await _git("init", "-b", "main", cwd=proj)
    await _git("config", "user.email", "t@t", cwd=proj)
    await _git("config", "user.name", "t", cwd=proj)
    (proj / "README.md").write_text("# x\n")
    await _git("add", "-A", cwd=proj)
    await _git("commit", "-qm", "init", cwd=proj)
    await _git("branch", "hive/sess/builder-0", cwd=proj)
    if producer_committed:
        await _git("worktree", "add", "-q", str(tmp_path / "wt"),
                   "hive/sess/builder-0", cwd=proj)
        (tmp_path / "wt" / "index.html").write_text("<html></html>\n")
        await _git("add", "-A", cwd=tmp_path / "wt")
        await _git("commit", "-qm", "built", cwd=tmp_path / "wt")
        await _git("worktree", "remove", "--force", str(tmp_path / "wt"), cwd=proj)
    return str(proj)


def _tester_agent():
    return SpawnedAgent(agent_id="tester-0", role="Tester", model="claude:sonnet",
                        worktree_path="/tmp/wt", files_hint=["index.html"], wave=1)


def _builder_agent():
    return SpawnedAgent(agent_id="builder-0", role="Builder", model="claude:sonnet",
                        worktree_path="/tmp/wt", files_hint=["index.html"], wave=0)


@pytest.mark.asyncio
async def test_missing_input_fails_fast_with_named_file(tmp_path) -> None:
    from backend.orchestrator import graph as gmod
    proj = await _repo(tmp_path, producer_committed=False)
    state = {"session_id": "sess", "project_path": proj}
    reason = await gmod._missing_consumed_input(
        _tester_agent(), [_builder_agent(), _tester_agent()], state)
    assert reason is not None
    assert "index.html" in reason and "builder-0" in reason


@pytest.mark.asyncio
async def test_present_input_proceeds(tmp_path) -> None:
    from backend.orchestrator import graph as gmod
    proj = await _repo(tmp_path, producer_committed=True)
    state = {"session_id": "sess", "project_path": proj}
    reason = await gmod._missing_consumed_input(
        _tester_agent(), [_builder_agent(), _tester_agent()], state)
    assert reason is None


@pytest.mark.asyncio
async def test_wave0_agent_never_failfast(tmp_path) -> None:
    from backend.orchestrator import graph as gmod
    proj = await _repo(tmp_path, producer_committed=False)
    state = {"session_id": "sess", "project_path": proj}
    # A wave-0 agent produces its own files — nothing to wait for.
    reason = await gmod._missing_consumed_input(
        _builder_agent(), [_builder_agent()], state)
    assert reason is None


@pytest.mark.asyncio
async def test_own_output_not_treated_as_consumed(tmp_path) -> None:
    """A file only THIS agent lists is its output, not a consumed input —
    even at wave>0 (no lower-wave producer), it must not fail-fast."""
    from backend.orchestrator import graph as gmod
    proj = await _repo(tmp_path, producer_committed=False)
    state = {"session_id": "sess", "project_path": proj}
    lone = SpawnedAgent(agent_id="writer-0", role="Writer", model="claude:sonnet",
                        worktree_path="/tmp/wt", files_hint=["NOTES.md"], wave=1)
    reason = await gmod._missing_consumed_input(lone, [lone], state)
    assert reason is None
