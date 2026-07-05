"""D5 — golden suite: spec parsing, criteria checks, report diffing (mocked)."""
from __future__ import annotations

from pathlib import Path

from backend.golden.runner import (
    Criterion,
    GoldenSpec,
    check_criteria,
    diff_reports,
    load_specs,
    previous_report,
    write_report,
)

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "golden"


def test_repo_specs_parse() -> None:
    specs = load_specs()
    names = {s.name for s in specs}
    assert {"tiny-fix", "flask-todo-api", "palette-playwright",
            "snake-game", "lessons-injection", "ambiguous-task",
            "multi-file-python", "docs-only"} <= names
    for spec in specs:
        assert spec.prompt and spec.criteria


def test_load_only_filter() -> None:
    specs = load_specs(only="tiny-fix")
    assert [s.name for s in specs] == ["tiny-fix"]


def test_criteria_checks(tmp_path) -> None:
    (tmp_path / "app.py").write_text("routes for /todos here")
    spec = GoldenSpec(name="t", prompt="p", criteria=[
        Criterion(type="file_exists", path="app.py"),
        Criterion(type="file_contains", path="app.py", text="/todos"),
        Criterion(type="command_succeeds", command="true"),
    ])
    ok, failures = check_criteria(spec, tmp_path)
    assert ok and failures == []

    spec_bad = GoldenSpec(name="t", prompt="p", criteria=[
        Criterion(type="file_exists", path="missing.py"),
        Criterion(type="file_contains", path="app.py", text="nope"),
        Criterion(type="command_succeeds", command="false"),
    ])
    ok, failures = check_criteria(spec_bad, tmp_path)
    assert not ok and len(failures) == 3


def test_report_write_and_diff(tmp_path) -> None:
    reports = tmp_path / "reports"
    first = [
        {"name": "a", "success": True, "failures": [], "wall_seconds": 60.0,
         "cost_usd": 0.50, "input_tokens": 1, "output_tokens": 2,
         "agents_spawned": 2, "session_id": "s1"},
        {"name": "b", "success": False, "failures": ["boom"], "wall_seconds": 30.0,
         "cost_usd": 0.20, "input_tokens": 1, "output_tokens": 2,
         "agents_spawned": 1, "session_id": "s2"},
    ]
    p1 = write_report(first, reports_dir=reports)
    assert p1.exists()

    import json
    second = [
        {**first[0], "success": False, "failures": ["broke"], "cost_usd": 0.80},
        {**first[1], "success": True, "failures": []},
    ]
    p2 = write_report(second, reports_dir=reports)
    current = json.loads(p2.read_text())
    prev = previous_report(reports_dir=reports, before=p2)
    assert prev is not None and prev["results"][0]["name"] == "a"

    lines = diff_reports(current, prev)
    joined = "\n".join(lines)
    assert "REGRESSION" in joined and "a" in joined     # pass → fail flagged
    assert "fixed" in joined                            # fail → pass noted


def test_diff_without_previous_is_baseline(tmp_path) -> None:
    lines = diff_reports({"results": []}, None)
    assert "baseline" in lines[0]


# ── D7: trajectory endpoint ─────────────────────────────────────────────────


def test_trajectory_endpoint_orders_and_types() -> None:
    import asyncio as _asyncio

    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.persistence.db import DB_PATH, init_db
    from backend.persistence.events import create_session, write_event
    from backend.workers.base import EventType, HiveEvent

    async def seed():
        await init_db(DB_PATH)
        await create_session("traj-1")
        await write_event(HiveEvent(type=EventType.AGENT_START, agent_id="b-0",
                                    session_id="traj-1", ts=10.0))
        await write_event(HiveEvent(type=EventType.VALIDATION_FAILED, agent_id="b-0",
                                    session_id="traj-1", ts=30.0,
                                    raw_payload={"findings": ["claims x"]}))
        await write_event(HiveEvent(type=EventType.AGENT_ERROR, agent_id="b-0",
                                    session_id="traj-1", ts=20.0,
                                    error="boom", origin="infrastructure"))
        await write_event(HiveEvent(type=EventType.REVIEW_LLM, agent_id="reviewer",
                                    session_id="traj-1", ts=40.0,
                                    raw_payload={"notes": ["fixed it"]}))
        await write_event(HiveEvent(type=EventType.COMPACTION, agent_id="orchestrator",
                                    session_id="traj-1", ts=50.0,
                                    raw_payload={"pruned_turns": 5, "state_doc": "d",
                                                 "pruned": []}))

    _asyncio.get_event_loop_policy().new_event_loop().run_until_complete(seed())

    with TestClient(app) as client:
        resp = client.get("/api/sessions/traj-1/trajectory")
    assert resp.status_code == 200
    nodes = resp.json()["trajectory"]
    ts_list = [n["ts"] for n in nodes]
    assert ts_list == sorted(ts_list)                       # chronological
    cats = [n["category"] for n in nodes]
    assert {"lifecycle", "error", "validation", "review", "compaction"} <= set(cats)
    err = next(n for n in nodes if n["category"] == "error")
    assert "infrastructure" in err["title"]                 # D0.2 origin visible

    with TestClient(app) as client:
        resp = client.get("/api/sessions/nope-x/trajectory")
    assert resp.status_code == 404
