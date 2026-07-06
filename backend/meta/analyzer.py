"""META analysis (D8): assemble the numbers in code, spend Opus once on judgment.

On-demand only — never scheduled. Input assembly reads the lessons store,
trust scores (split by origin, D0.2), failure clusters from events, cost
breakdowns, estimate-vs-actual drift (D6), and golden trends (D5). One
Opus call turns that into META_REPORT.md. Nothing auto-executes; lesson
recommendations go through the same D1.3 gate as everything else.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from backend.persistence.db import DB_PATH, get_conn

logger = logging.getLogger(__name__)


async def assemble_inputs(
    project_path: str | None = None, db_path: Path = DB_PATH
) -> dict:
    """Pure-code aggregation — no LLM involvement in the numbers."""
    out: dict = {"scope": project_path or "global"}

    async with get_conn(db_path) as conn:
        # Lessons stats (D1).
        cursor = await conn.execute(
            "SELECT status, origin, COUNT(*) AS n, "
            "SUM(times_applied) AS applied, SUM(times_confirmed) AS confirmed, "
            "SUM(times_unconfirmed) AS unconfirmed "
            "FROM lessons GROUP BY status, origin")
        out["lessons"] = [dict(r) for r in await cursor.fetchall()]

        # Trust by worker (D0.2-honest since the reset).
        cursor = await conn.execute(
            "SELECT worker_id, successful_completions, failed_validations, "
            "total_sessions FROM worker_trust_scores ORDER BY total_sessions DESC")
        out["trust"] = [dict(r) for r in await cursor.fetchall()]

        # Failure clusters: errors grouped by origin + a coarse class (first
        # 60 chars of the error), validation failures by finding prefix.
        cursor = await conn.execute(
            "SELECT payload_json FROM events WHERE type='agent/error'")
        clusters: dict[tuple[str, str], int] = {}
        for row in await cursor.fetchall():
            payload = json.loads(row["payload_json"])
            origin = payload.get("origin") or "unknown"
            klass = (payload.get("error") or "")[:60]
            clusters[(origin, klass)] = clusters.get((origin, klass), 0) + 1
        out["failure_clusters"] = [
            {"origin": o, "error_class": k, "count": n}
            for (o, k), n in sorted(clusters.items(), key=lambda kv: -kv[1])
        ][:15]

        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE type='validation/failed'")
        out["validation_failures"] = (await cursor.fetchone())["n"]
        cursor = await conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE type='review/llm'")
        out["llm_review_interventions"] = (await cursor.fetchone())["n"]

        # Cost by model and by role.
        cursor = await conn.execute(
            """SELECT a.model, a.role,
                      COUNT(DISTINCT a.id) AS agents,
                      (SELECT COALESCE(SUM(cost_usd),0) FROM cost_log c
                       WHERE c.agent_id = a.id) AS cost
               FROM agents a GROUP BY a.model, a.role ORDER BY cost DESC""")
        out["cost_breakdown"] = [dict(r) for r in await cursor.fetchall()][:15]

        # Estimate-vs-actual drift (D6).
        cursor = await conn.execute(
            "SELECT payload_json FROM events WHERE type='estimate/actual'")
        drifts = []
        for row in await cursor.fetchall():
            payload = json.loads(row["payload_json"]).get("raw_payload") or {}
            est = (payload.get("estimate") or {}).get("cost_median_usd")
            actual = payload.get("actual_cost_usd")
            if est is not None and actual is not None:
                drifts.append(round(actual - est, 2))
        out["estimate_drift"] = {
            "samples": len(drifts),
            "mean_delta_usd": round(sum(drifts) / len(drifts), 2) if drifts else None,
        }

    # Golden trends (D5) — last two reports when present.
    reports_dir = Path(__file__).resolve().parents[2] / "golden" / "reports"
    reports = sorted(reports_dir.glob("golden-*.json"))[-2:] if reports_dir.exists() else []
    out["golden"] = [json.loads(p.read_text()) for p in reports]
    return out


_PROMPT = """You are META, the strategic advisor for HIVE (a personal multi-agent coding
orchestrator). Below are AGGREGATED, OBJECTIVE numbers from its own database. Produce
META_REPORT.md. Ground every claim in the numbers — no invention. Scope: {scope}.

DATA:
{data}

Structure exactly:
# META Report — {date}
## 1. What's working
## 2. Recurring failure clusters (evidence counts, split infrastructure vs agent)
## 3. Recommendations (ranked by expected impact; tag each [config change] |
   [lesson to add] | [HIVE code change] | [prompt change]; for [lesson to add]
   include the lesson's title/content/trigger_context inline)
## 4. Roadmap: 3-5 next steps

Be concrete and honest — 'insufficient data' is a valid observation."""


async def _opus_call(prompt: str, session_id: str = "meta") -> str:
    """One-shot Opus — the one place the strong model earns its cost."""
    from backend.models import OPUS_MODEL
    from backend.workers.base import EventType, WorkerConfig
    from backend.workers.claude_cli import ClaudeCLIWorker

    worker = ClaudeCLIWorker()
    config = WorkerConfig(
        agent_id=f"meta-{session_id}", session_id=session_id,
        # E0.4: claude CLI counts every tool iteration as a turn, so
        # max_turns=1 with an allowed Read tool killed the first live
        # META run at num_turns=2 (same bug class as the E0.3 planner
        # fix). 4 turns = a Read or two, then the report.
        model=OPUS_MODEL, worktree_path="/tmp", max_turns=4,
        allowed_tools=["Read"],
    )
    chunks: list[str] = []
    final: str | None = None
    async for event in worker.run(prompt, config):
        if event.type == EventType.TEXT_DELTA and event.text:
            chunks.append(event.text)
        elif event.type == EventType.TEXT_DONE and event.text:
            final = event.text
        elif event.type == EventType.COST:
            # E0.4: META's own Opus cost was invisible to cost accounting
            # (the same gap E0.2 closed for llm_review).
            try:
                from backend.persistence.events import write_cost
                await write_cost(session_id, config.agent_id,
                                 event.input_tokens or 0,
                                 event.output_tokens or 0,
                                 event.cost_usd or 0.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("META cost write failed: %s", exc)
        elif event.type == EventType.AGENT_ERROR:
            raise RuntimeError(f"META Opus call failed: {event.error}")
    return (final if final is not None else "".join(chunks)).strip()


async def run_meta(
    project_path: str | None = None,
    opus_caller=None,
    db_path: Path = DB_PATH,
) -> tuple[str, Path]:
    """Assemble → one Opus call → persist META_REPORT.md. Returns (report, path)."""
    inputs = await assemble_inputs(project_path, db_path=db_path)
    prompt = _PROMPT.format(
        scope=inputs["scope"],
        date=time.strftime("%Y-%m-%d"),
        data=json.dumps(inputs, indent=1, default=str)[:20000],
    )
    # cost_log rows FK to sessions — give META runs a real session row so
    # the Opus cost is actually recorded (E0.4).
    meta_session = f"meta{int(time.time()) % 100_000_000:08d}"
    try:
        from backend.persistence.events import create_session
        await create_session(meta_session, name="META analysis",
                             path=str(project_path or ""), db_path=db_path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("META session row create failed: %s", exc)

    caller = opus_caller or _opus_call
    report = await caller(prompt) if opus_caller else await _opus_call(
        prompt, session_id=meta_session)
    if not report.strip():
        raise RuntimeError("META produced an empty report")

    target_dir = Path(project_path) if project_path else Path(__file__).resolve().parents[2]
    report_path = target_dir / "META_REPORT.md"
    report_path.write_text(report + "\n")
    logger.info("META report written to %s", report_path)
    return report, report_path
