"""Golden suite runner (D5).

Fixed task specs (golden/*.yaml) executed through the REAL pipeline (real
models, real cost) — a manual, on-demand tool for judging prompt/model
changes, deliberately NOT CI. Each run writes a timestamped JSON report to
golden/reports/ and prints a comparison against the previous report.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"
REPORTS_DIR = GOLDEN_DIR / "reports"


@dataclass
class Criterion:
    type: str                    # file_exists | file_contains | command_succeeds
    path: str = ""
    text: str = ""
    command: str = ""


@dataclass
class GoldenSpec:
    name: str
    prompt: str
    criteria: list[Criterion]
    workspace_fixture: dict[str, str] = field(default_factory=dict)
    timeout_minutes: float = 15.0
    # E5: init the workspace as a git repo on this branch (fixture files
    # committed) BEFORE the session — specs whose premise involves the
    # repo's git state need it to exist at PLANNING time, not spawn time.
    git_branch: str = ""


def load_specs(only: str | None = None, golden_dir: Path = GOLDEN_DIR) -> list[GoldenSpec]:
    specs: list[GoldenSpec] = []
    for path in sorted(golden_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        spec = GoldenSpec(
            name=data["name"],
            prompt=data["prompt"].strip(),
            criteria=[Criterion(**c) for c in data.get("success_criteria", [])],
            workspace_fixture=data.get("workspace_fixture") or {},
            timeout_minutes=float(data.get("timeout_minutes", 15)),
            git_branch=str(data.get("git_branch") or ""),
        )
        if only and spec.name != only:
            continue
        specs.append(spec)
    return specs


def check_criteria(spec: GoldenSpec, workspace: Path) -> tuple[bool, list[str]]:
    """Executable success checks against the finished workspace."""
    failures: list[str] = []
    for c in spec.criteria:
        if c.type == "file_exists":
            if not (workspace / c.path).exists():
                failures.append(f"file_exists: {c.path} missing")
        elif c.type == "file_contains":
            target = workspace / c.path
            if not target.exists():
                failures.append(f"file_contains: {c.path} missing")
            elif c.text not in target.read_text(errors="replace"):
                failures.append(f"file_contains: {c.text!r} not in {c.path}")
        elif c.type == "command_succeeds":
            proc = subprocess.run(
                c.command, shell=True, cwd=workspace,
                capture_output=True, text=True, timeout=300,
            )
            if proc.returncode != 0:
                tail = (proc.stdout + proc.stderr)[-300:]
                failures.append(f"command_succeeds: `{c.command}` exited "
                                f"{proc.returncode}: {tail}")
        else:
            failures.append(f"unknown criterion type {c.type!r}")
    return not failures, failures


async def run_spec(spec: GoldenSpec) -> dict:
    """One spec through the real pipeline: run_session → close, then checks."""
    from backend.orchestrator.graph import (
        SessionInterrupt,
        resume_session_with_value,
        run_session,
    )
    from backend.persistence.db import DB_PATH, get_conn, init_db

    await init_db(DB_PATH)
    workspace = Path(tempfile.mkdtemp(prefix=f"golden-{spec.name}-"))
    for rel, content in spec.workspace_fixture.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    if spec.git_branch:
        for cmd in (["git", "init", "-b", spec.git_branch],
                    ["git", "config", "user.email", "golden@hive"],
                    ["git", "config", "user.name", "golden"],
                    ["git", "add", "-A"],
                    ["git", "commit", "-m", "fixture", "--allow-empty"]):
            subprocess.run(cmd, cwd=workspace, check=True, capture_output=True)

    session_id = f"g{uuid.uuid4().hex[:7]}"
    started = time.time()
    error: str | None = None
    try:
        result = await asyncio.wait_for(
            run_session(
                session_id=session_id, agent_id=f"golden-{spec.name}",
                task=spec.prompt, model="claude:sonnet",
                worktree_path=str(workspace),
            ),
            timeout=spec.timeout_minutes * 60,
        )
        # Approval interrupts auto-approve (golden runs are unattended);
        # the awaiting-input park closes the session.
        while isinstance(result, SessionInterrupt):
            if result.payload.get("type") == "team_approval":
                value: dict = {"approved": True}
            else:
                value = {"close": True}
            result = await asyncio.wait_for(
                resume_session_with_value(session_id, value),
                timeout=spec.timeout_minutes * 60,
            )
    except asyncio.TimeoutError:
        error = f"timeout after {spec.timeout_minutes} min"
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    wall_s = time.time() - started

    passed, failures = (False, [error]) if error else check_criteria(spec, workspace)

    async with get_conn(DB_PATH) as conn:
        cursor = await conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0), COALESCE(SUM(input_tokens),0), "
            "COALESCE(SUM(output_tokens),0) FROM cost_log WHERE session_id=?",
            (session_id,))
        cost, tin, tout = await cursor.fetchone()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM agents WHERE session_id=?", (session_id,))
        (agents,) = await cursor.fetchone()

    shutil.rmtree(workspace, ignore_errors=True)
    return {
        "name": spec.name, "success": passed, "failures": failures,
        "wall_seconds": round(wall_s, 1), "cost_usd": round(cost, 4),
        "input_tokens": tin, "output_tokens": tout,
        "agents_spawned": agents, "session_id": session_id,
    }


def write_report(results: list[dict], reports_dir: Path = REPORTS_DIR) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = reports_dir / f"golden-{stamp}.json"
    seq = 1
    while path.exists():  # same-second runs must not overwrite each other
        path = reports_dir / f"golden-{stamp}-{seq}.json"
        seq += 1
    path.write_text(json.dumps({
        "timestamp": stamp,
        "results": results,
        "total_cost_usd": round(sum(r["cost_usd"] for r in results), 4),
        "passed": sum(1 for r in results if r["success"]),
        "total": len(results),
    }, indent=2))
    return path


def previous_report(reports_dir: Path = REPORTS_DIR, before: Path | None = None) -> dict | None:
    if not reports_dir.exists():
        return None
    reports = sorted(reports_dir.glob("golden-*.json"))
    if before is not None:
        reports = [r for r in reports if r != before]
    if not reports:
        return None
    return json.loads(reports[-1].read_text())


def diff_reports(current: dict, previous: dict | None) -> list[str]:
    """Human-readable comparison; regressions prefixed with ✗."""
    lines: list[str] = []
    if previous is None:
        lines.append("(no previous report — this run is the new baseline)")
        return lines
    prev_by_name = {r["name"]: r for r in previous["results"]}
    for r in current["results"]:
        prev = prev_by_name.get(r["name"])
        if prev is None:
            lines.append(f"+ {r['name']}: new task")
            continue
        if prev["success"] and not r["success"]:
            lines.append(f"✗ {r['name']}: REGRESSION — passed before, fails now: "
                         f"{'; '.join(r['failures'])}")
        elif not prev["success"] and r["success"]:
            lines.append(f"✓ {r['name']}: fixed (failed in previous run)")
        else:
            delta_cost = r["cost_usd"] - prev["cost_usd"]
            delta_time = r["wall_seconds"] - prev["wall_seconds"]
            lines.append(
                f"  {r['name']}: {'pass' if r['success'] else 'fail'} "
                f"(cost {r['cost_usd']:+.2f}Δ{delta_cost:+.2f}, "
                f"time {r['wall_seconds']:.0f}sΔ{delta_time:+.0f})")
    return lines
