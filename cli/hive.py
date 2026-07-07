"""HIVE CLI — entry point for all hive commands.

Commands:
    hive start                   start the HIVE backend API server
    hive run "task" [--backend claude:sonnet] [--approval-mode full-auto|checkpoint|manual]
    hive status                  show available backends
    hive sessions                list recent sessions
    hive resume <id>             resume a crashed or interrupted session
    hive skills list             list registered skills
    hive skills import <path>    import a SKILL.md into the registry
    hive skills create <name>    create a new SKILL.md template
    hive skills test <query>     test semantic search for a query
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

import typer

app = typer.Typer(help="HIVE — AI agent swarm orchestration")
skills_app = typer.Typer(help="Manage HIVE skills")
pipelines_app = typer.Typer(help="Manage HIVE persistent pipelines (Phase 6)")
telegram_app = typer.Typer(help="Configure the HIVE Telegram bot (Phase 7)")
app.add_typer(skills_app, name="skills")
app.add_typer(pipelines_app, name="pipelines")
app.add_typer(telegram_app, name="telegram")

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

_APPROVAL_MODES = ("full-auto", "checkpoint", "manual")


@app.command()
def run(
    task: str = typer.Argument(..., help="Task prompt for the agent"),
    backend: str = typer.Option(
        "claude:sonnet",
        "--backend", "-b",
        help="Backend: claude:sonnet | claude:opus | claude:haiku | ollama:<model>",
    ),
    approval_mode: str = typer.Option(
        "full-auto",
        "--approval-mode", "-a",
        help="full-auto (default) | checkpoint (always ask) | manual (ask + verbose)",
    ),
    cwd: str = typer.Option(None, "--cwd", help="Working directory (default: current)"),
    max_turns: int = typer.Option(20, "--max-turns"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    no_persist: bool = typer.Option(False, "--no-persist", help="Skip SQLite (raw worker mode)"),
) -> None:
    """Run a task with the HIVE agent swarm."""
    if verbose:
        logging.getLogger().setLevel(logging.INFO)
    if approval_mode not in _APPROVAL_MODES:
        typer.echo(f"[error] Unknown approval mode '{approval_mode}'. Choose: {', '.join(_APPROVAL_MODES)}", err=True)
        raise typer.Exit(code=1)
    asyncio.run(_run_async(task, backend, cwd or os.getcwd(), max_turns, verbose, no_persist, approval_mode))


@app.command()
def status() -> None:
    """Show which backends are available on this machine."""
    asyncio.run(_status_async())


golden_app = typer.Typer(help="Golden regression suite (D5) — real models, real cost")
app.add_typer(golden_app, name="golden")

models_app = typer.Typer(help="Local (Ollama) model pool — list, audition")
app.add_typer(models_app, name="models")


@models_app.command("list")
def models_list() -> None:
    """Discovered local models with capabilities and provenance."""
    import asyncio as _asyncio

    from backend.models_local import discover_local_models
    from backend.persistence.db import init_db

    async def _run():
        await init_db()
        return await discover_local_models()

    models = _asyncio.run(_run())
    if not models:
        typer.echo("No local models (is Ollama running?)")
        raise typer.Exit(code=0)
    for m in models:
        avail = "available" if m.available else f"unavailable ({m.unavailable_reason})"
        typer.echo(f"{m.name:32s} {m.size_gb:5.1f}GB  [{m.provenance:8s}] "
                   f"{', '.join(sorted(m.capabilities))} — {avail}")


@models_app.command("audition")
def models_audition(model: str) -> None:
    """Measure a local model's real capabilities with fixed micro-tasks
    (code+pytest, summary graded by Haiku, classification exact-match).
    Results override name/metadata inference in the planner digest."""
    import asyncio as _asyncio

    from backend.models_local import audition_model
    from backend.persistence.db import init_db

    typer.echo(f"Auditioning {model} — 3 micro-tasks, local generation "
               "($0) + one tiny Haiku grade…")

    async def _run():
        await init_db()
        return await audition_model(model)

    measured = _asyncio.run(_run())
    results = measured.get("results", {})
    cls = results.get("classification", {})
    summ = results.get("summarization", {})
    code = results.get("coding", {})
    typer.echo(f"  classification: {cls.get('score')}/5  "
               f"{'PASS' if cls.get('passed') else 'fail'}")
    typer.echo(f"  summarization:  {summ.get('score')}/10 "
               f"{'PASS' if summ.get('passed') else 'fail'}")
    typer.echo(f"  coding(pytest): {'PASS' if code.get('passed') else 'fail'}")
    caps = ", ".join(measured.get("capabilities") or []) or "(none)"
    typer.echo(f"Measured capabilities stored: {caps}")


@golden_app.command("run")
def golden_run(
    only: str = typer.Option(None, "--only", help="Run a single spec by name"),
) -> None:
    """Execute golden specs through the real pipeline and diff vs last report."""

    async def _run() -> int:
        from backend.detection import detect_backends
        from backend.golden.runner import (
            diff_reports,
            load_specs,
            previous_report,
            run_spec,
            write_report,
        )

        # E5: resolve the Ollama endpoint (WSL host-IP fallback) so hybrid
        # routing sees the local pool — without this, discovery probes
        # localhost, finds nothing, and every run silently goes Claude-only.
        status = await detect_backends()
        if status.ollama:
            typer.echo(f"Local pool: {', '.join(status.ollama_models) or '(none)'}")

        specs = load_specs(only=only)
        if not specs:
            typer.echo(f"No specs matched{f' --only {only}' if only else ''}.")
            return 1
        typer.echo(f"Running {len(specs)} golden spec(s) — real models, real cost.\n")
        results = []
        for spec in specs:
            typer.echo(f"▶ {spec.name} …")
            result = await run_spec(spec)
            mark = "✓" if result["success"] else "✗"
            typer.echo(
                f"{mark} {spec.name}: {result['wall_seconds']:.0f}s, "
                f"${result['cost_usd']:.2f}, {result['agents_spawned']} agent(s)"
            )
            for failure in result["failures"]:
                typer.echo(f"    - {failure}")
            results.append(result)

        report_path = write_report(results)
        current = __import__("json").loads(report_path.read_text())
        prev = previous_report(before=report_path)
        typer.echo(f"\nReport: {report_path}")
        for line in diff_reports(current, prev):
            typer.echo(line)
        return 0 if all(r["success"] for r in results) else 1

    raise typer.Exit(code=asyncio.run(_run()))


@app.command()
def meta(
    project: str = typer.Option(None, "--project", help="Project path (default: global)"),
) -> None:
    """Run the META analysis — one Opus call over HIVE's own stats (D8).

    Cost note: one Opus analysis, roughly $0.10-0.50 depending on history size.
    """
    async def _run() -> None:
        from backend.meta.analyzer import run_meta
        report, path = await run_meta(project)
        typer.echo(report)
        typer.echo(f"\nSaved to {path}")

    asyncio.run(_run())


@app.command()
def doctor() -> None:
    """Live-check every MCP catalog server: spawn + initialize handshake (D0.3)."""

    async def _run() -> int:
        from backend.mcp.catalog import list_specs, preflight
        from backend.mcp.doctor import check_server

        failures = 0
        for spec in list_specs():
            static_missing = preflight(spec)
            if static_missing:
                typer.echo(f"✗ {spec.id:<12} {'; '.join(static_missing)}")
                failures += 1
                continue
            ok, detail = await check_server(spec, use_cache=False)
            mark = "✓" if ok else "✗"
            typer.echo(f"{mark} {spec.id:<12} {detail}")
            failures += 0 if ok else 1
        return failures

    fails = asyncio.run(_run())
    if fails:
        raise typer.Exit(code=1)


@app.command()
def onboard() -> None:
    """First-run setup wizard — sanity-checks the environment for HIVE."""
    from backend.onboarding import render_report, run_onboarding
    checks = asyncio.run(run_onboarding())
    typer.echo(render_report(checks))
    fails = sum(1 for c in checks if not c.ok)
    if fails:
        raise typer.Exit(code=1)


@app.command()
def sessions(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of sessions to show"),
) -> None:
    """List recent sessions."""
    asyncio.run(_sessions_async(limit))


@app.command()
def resume(
    session_id: str = typer.Argument(..., help="Session ID to resume"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Resume a crashed or interrupted session from its last checkpoint."""
    if verbose:
        logging.getLogger().setLevel(logging.INFO)
    asyncio.run(_resume_async(session_id, verbose))


# ── async implementations ────────────────────────────────────────────────────

async def _run_async(
    task: str,
    backend: str,
    cwd: str,
    max_turns: int,
    verbose: bool,
    no_persist: bool,
    approval_mode: str,
) -> None:
    from backend.detection import detect_backends
    from backend.orchestrator.graph import SessionInterrupt, resume_session_with_value, run_session
    from backend.persistence.db import init_db
    from backend.persistence.recovery import run_startup_recovery
    from backend.workers.base import WorkerConfig

    await init_db()
    crashed = await run_startup_recovery()
    if crashed:
        typer.echo(f"[recovery] {len(crashed)} agent(s) from previous session marked as crashed.")

    availability = await detect_backends()
    if not _backend_available(backend, availability):
        typer.echo(f"[error] Backend '{backend}' is not available.", err=True)
        typer.echo(f"Available: {availability.summary()}", err=True)
        raise typer.Exit(code=1)

    session_id = str(uuid.uuid4())[:8]
    agent_id = f"agent-{session_id}"

    if no_persist:
        typer.echo(f"Session: {session_id}  |  Backend: {backend}")
        typer.echo(f"Task: {task}")
        typer.echo("─" * 60)
        worker = _make_raw_worker(backend)
        config = WorkerConfig(
            agent_id=agent_id, session_id=session_id, model=backend,
            worktree_path=cwd, max_turns=max_turns,
        )
        async for event in worker.run(task, config):
            _print_event(event, verbose)
        return

    typer.echo(f"Session: {session_id}  |  Backend: {backend}  |  Approval: {approval_mode}")
    typer.echo(f"Task: {task}")
    typer.echo("─" * 60)

    result = await run_session(
        session_id=session_id,
        agent_id=agent_id,
        task=task,
        model=backend,
        worktree_path=cwd,
        max_turns=max_turns,
        approval_mode=approval_mode,
    )

    if isinstance(result, SessionInterrupt):
        result = await _handle_interrupt(result)
        if result is None:
            return

    _print_result(result, session_id)


async def _resume_async(session_id: str, verbose: bool) -> None:
    from backend.orchestrator.graph import SessionInterrupt, resume_session
    from backend.persistence.db import init_db
    from backend.persistence.events import get_session

    await init_db()
    session = await get_session(session_id)
    if not session:
        typer.echo(f"[error] Session '{session_id}' not found.", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Resuming session: {session_id} ({session['name']})")

    result = await resume_session(session_id)

    if result is None:
        typer.echo("[error] No checkpoint found — cannot resume.", err=True)
        raise typer.Exit(code=1)

    if isinstance(result, SessionInterrupt):
        result = await _handle_interrupt(result)
        if result is None:
            return

    _print_result(result, session_id)


async def _status_async() -> None:
    from backend.detection import detect_backends

    availability = await detect_backends()
    typer.echo("HIVE Backend Status")
    typer.echo("=" * 40)
    if availability.claude_cli:
        typer.echo(f"Claude CLI:  ok  {availability.claude_cli_version}")
    else:
        typer.echo("Claude CLI:  not found")
    typer.echo(f"Claude API:  {'ok  ANTHROPIC_API_KEY set' if availability.claude_api else 'no API key'}")
    if availability.ollama:
        models = ", ".join(availability.ollama_models) or "(no models pulled)"
        typer.echo(f"Ollama:      ok  models: {models}")
    else:
        typer.echo("Ollama:      not running  (start with `ollama serve`)")


async def _sessions_async(limit: int) -> None:
    from backend.persistence.db import init_db
    from backend.persistence.events import list_sessions

    await init_db()
    rows = await list_sessions(limit=limit)
    if not rows:
        typer.echo("No sessions found.")
        return

    typer.echo(f"{'ID':<10} {'Status':<11} {'Name':<45} Last active")
    typer.echo("─" * 85)
    for r in rows:
        typer.echo(f"{r['id']:<10} {r['status']:<11} {r['name'][:44]:<45} {r['last_active']}")


# ── approval UI ──────────────────────────────────────────────────────────────

async def _handle_interrupt(si) -> object:
    """Display the pending approval prompt and resume the session."""
    from backend.orchestrator.graph import resume_session_with_value

    payload = si.payload
    comp = payload.get("team_composition", {})
    confidence = payload.get("confidence", 1.0)
    reason = payload.get("reason", "")

    typer.echo()
    typer.echo("┌─ HIVE: Approval Required " + "─" * 34)
    if reason == "low_confidence":
        typer.echo(f"│  Planner confidence is LOW ({confidence:.0%}) — review before proceeding.")
    else:
        typer.echo("│  Checkpoint: review team composition before agents launch.")
    typer.echo("│")
    typer.echo("│  Proposed team:")
    for m in comp.get("team", []):
        passive_tag = " (passive)" if m.get("passive") else ""
        typer.echo(f"│    {m['role']:<12} x{m.get('count', 1)}  [{m.get('model', '?')}]{passive_tag}")
    typer.echo("│")
    typer.echo(f"│  Confidence: {confidence:.0%}  |  Rationale: {comp.get('rationale', '')}")
    typer.echo("└" + "─" * 60)
    typer.echo()

    approved = typer.confirm("Approve this team and proceed?", default=True)

    if not approved:
        await resume_session_with_value(
            session_id=si.session_id,
            resume_value={"approved": False},
        )
        typer.echo("Task cancelled.")
        return None

    return await resume_session_with_value(
        session_id=si.session_id,
        resume_value={"approved": True},
    )


def _print_result(result, session_id: str) -> None:
    if not result:
        typer.echo("[error] No result returned.", err=True)
        raise typer.Exit(code=1)

    status = result.get("status", "unknown")
    if status == "cancelled":
        typer.echo("Task was cancelled.")
        return

    print(result.get("text_output", ""), end="")
    print()
    cost_usd = result.get("cost_usd", 0.0)
    cost_str = f"${cost_usd:.4f}" if cost_usd else "$0.00 (local)"
    typer.echo("\n" + "─" * 60)
    typer.echo(
        f"Tokens: {result.get('input_tokens', 0)} in / {result.get('output_tokens', 0)} out"
        f"  |  Cost: {cost_str}"
    )
    if status not in ("completed", "cancelled"):
        typer.echo(f"[{status}] {result.get('error', '')}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"\nSession saved. Resume with: hive resume {session_id}")


# ── helpers ──────────────────────────────────────────────────────────────────

def _backend_available(backend: str, availability) -> bool:
    if backend.startswith("ollama:"):
        return availability.ollama
    if backend.startswith("claude:"):
        return availability.claude_cli or availability.claude_api
    return False


def _make_raw_worker(backend: str):
    from backend.workers.claude_cli import ClaudeCLIWorker
    from backend.workers.ollama import OllamaWorker
    if backend.startswith("ollama:"):
        return OllamaWorker()
    return ClaudeCLIWorker()


def _print_event(event, verbose: bool) -> None:
    from backend.workers.base import EventType
    if event.type == EventType.TEXT_DELTA and event.text:
        print(event.text, end="", flush=True)
    elif event.type == EventType.COST:
        print()
        cost_str = f"${event.cost_usd:.4f}" if event.cost_usd else "$0.00 (local)"
        typer.echo("\n" + "─" * 60)
        typer.echo(f"Tokens: {event.input_tokens} in / {event.output_tokens} out  |  Cost: {cost_str}")
    elif event.type == EventType.RATE_LIMIT:
        typer.echo(f"\n[rate_limit] Waiting {event.retry_after_ms}ms...", err=True)
    elif event.type == EventType.AGENT_ERROR:
        typer.echo(f"\n[error] {event.error}", err=True)
        raise typer.Exit(code=1)
    elif event.type == EventType.AGENT_END and verbose:
        typer.echo("[done]")


# ── skills commands ───────────────────────────────────────────────────────────

@skills_app.command("list")
def skills_list() -> None:
    """List all registered skills."""
    asyncio.run(_skills_list_async())


@skills_app.command("import")
def skills_import(
    path: str = typer.Argument(..., help="Path to SKILL.md file"),
) -> None:
    """Import a SKILL.md into the skills registry (embeds description)."""
    asyncio.run(_skills_import_async(path))


@skills_app.command("create")
def skills_create(
    name: str = typer.Argument(..., help="Skill name (e.g. 'python-testing')"),
    description: str = typer.Option(..., "--description", "-d", help="One-line description"),
    tags: str = typer.Option("", "--tags", "-t", help="Comma-separated tags"),
) -> None:
    """Create a new SKILL.md template at ~/.hive/skills/<name>/SKILL.md."""
    asyncio.run(_skills_create_async(name, description, tags))


@skills_app.command("test")
def skills_test(
    query: str = typer.Argument(..., help="Query to test semantic search"),
    top_k: int = typer.Option(5, "--top-k", "-k"),
    threshold: float = typer.Option(0.3, "--threshold"),
) -> None:
    """Test skill semantic search for a given query."""
    asyncio.run(_skills_test_async(query, top_k, threshold))


async def _skills_list_async() -> None:
    from backend.persistence.db import init_db
    from backend.skills.registry import list_skills

    await init_db()
    skills = await list_skills()
    if not skills:
        typer.echo("No skills registered. Use 'hive skills import' to add one.")
        return
    typer.echo(f"{'ID':<25} {'Name':<25} {'Tags'}")
    typer.echo("─" * 70)
    for s in skills:
        tags_str = ", ".join(s.tags) if s.tags else "—"
        typer.echo(f"{s.id:<25} {s.name:<25} {tags_str}")
    typer.echo(f"\n{len(skills)} skill(s) registered.")


async def _skills_import_async(path: str) -> None:
    from pathlib import Path
    from backend.persistence.db import init_db
    from backend.skills.registry import import_skill

    await init_db()
    skill_path = Path(path).expanduser().resolve()
    if not skill_path.exists():
        typer.echo(f"[error] File not found: {skill_path}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Importing {skill_path} ...")
    try:
        skill = await import_skill(skill_path)
        typer.echo(f"Imported skill '{skill.id}': {skill.description}")
    except Exception as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(code=1)


async def _skills_create_async(name: str, description: str, tags_str: str) -> None:
    from backend.persistence.db import init_db
    from backend.skills.registry import create_skill_file

    await init_db()
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
    skill_path = await create_skill_file(name=name, description=description, tags=tags)
    typer.echo(f"Created: {skill_path}")
    typer.echo("Edit the file, then run: hive skills import " + str(skill_path))


async def _skills_test_async(query: str, top_k: int, threshold: float) -> None:
    from backend.persistence.db import init_db
    from backend.skills.registry import search

    await init_db()
    typer.echo(f"Searching for: '{query}'  (top_k={top_k}, threshold={threshold:.2f})")
    typer.echo("─" * 60)

    try:
        results = await search(query, top_k=top_k, threshold=threshold)
    except RuntimeError as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(code=1)

    if not results:
        typer.echo("No matching skills found above threshold.")
        return

    for i, skill in enumerate(results, 1):
        typer.echo(f"{i}. [{skill.id}] {skill.name}")
        typer.echo(f"   {skill.description}")
        if skill.tags:
            typer.echo(f"   tags: {', '.join(skill.tags)}")
        typer.echo()


# ── pipelines commands ────────────────────────────────────────────────────────

@pipelines_app.command("list")
def pipelines_list() -> None:
    """List all persistent pipelines."""
    asyncio.run(_pipelines_list_async())


@pipelines_app.command("create")
def pipelines_create(
    name: str = typer.Argument(..., help="Pipeline name"),
    task: str = typer.Option(..., "--task", "-t", help="Task prompt to run"),
    schedule: str = typer.Option(None, "--schedule", "-s", help='Cron expression, e.g. "0 17 * * *"'),
    model: str = typer.Option("claude:sonnet", "--model", "-m"),
    approval_mode: str = typer.Option("full-auto", "--approval-mode", "-a"),
) -> None:
    """Create a new pipeline. Optionally schedule it with cron."""
    if approval_mode not in _APPROVAL_MODES:
        typer.echo(f"[error] Unknown approval mode '{approval_mode}'.", err=True)
        raise typer.Exit(code=1)
    asyncio.run(_pipelines_create_async(name, task, model, approval_mode, schedule))


@pipelines_app.command("delete")
def pipelines_delete(
    pipeline_id: str = typer.Argument(..., help="Pipeline ID"),
) -> None:
    """Delete a pipeline (and its schedule, if any)."""
    asyncio.run(_pipelines_delete_async(pipeline_id))


@pipelines_app.command("run")
def pipelines_run(
    pipeline_id: str = typer.Argument(..., help="Pipeline ID to fire immediately"),
) -> None:
    """Trigger a one-off run of a pipeline right now."""
    asyncio.run(_pipelines_run_async(pipeline_id))


@pipelines_app.command("runs")
def pipelines_runs(
    pipeline_id: str = typer.Argument(..., help="Pipeline ID"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Show recent runs for a pipeline."""
    asyncio.run(_pipelines_runs_async(pipeline_id, limit))


async def _pipelines_list_async() -> None:
    from backend.persistence.db import init_db
    from backend.pipelines.store import list_pipelines

    await init_db()
    pipelines = await list_pipelines()
    if not pipelines:
        typer.echo("No pipelines registered. Use 'hive pipelines create' to add one.")
        return
    typer.echo(f"{'ID':<14} {'Status':<9} {'Schedule':<15} {'Name'}")
    typer.echo("─" * 75)
    for p in pipelines:
        status = "enabled" if p["enabled"] else "disabled"
        schedule = p.get("schedule") or "manual"
        typer.echo(f"{p['id']:<14} {status:<9} {schedule:<15} {p['name']}")


async def _pipelines_create_async(
    name: str, task: str, model: str, approval_mode: str, schedule: str | None,
) -> None:
    from backend.persistence.db import init_db
    from backend.pipelines.store import create_pipeline, get_pipeline

    await init_db()
    pid = await create_pipeline(
        name=name, task=task, model=model, approval_mode=approval_mode, schedule=schedule,
    )
    p = await get_pipeline(pid)
    typer.echo(f"Created pipeline: {pid}")
    typer.echo(f"  name:     {p['name']}")
    typer.echo(f"  task:     {p['task']}")
    typer.echo(f"  schedule: {p.get('schedule') or '(manual)'}")
    typer.echo(f"  webhook:  /api/pipelines/webhook/{p['webhook_token']}")


async def _pipelines_delete_async(pipeline_id: str) -> None:
    from backend.persistence.db import init_db
    from backend.pipelines.store import delete_pipeline, get_pipeline

    await init_db()
    p = await get_pipeline(pipeline_id)
    if not p:
        typer.echo(f"[error] Pipeline '{pipeline_id}' not found.", err=True)
        raise typer.Exit(code=1)
    await delete_pipeline(pipeline_id)
    typer.echo(f"Deleted pipeline: {pipeline_id}")


async def _pipelines_run_async(pipeline_id: str) -> None:
    from backend.persistence.db import init_db
    from backend.pipelines.store import get_pipeline

    await init_db()
    p = await get_pipeline(pipeline_id)
    if not p:
        typer.echo(f"[error] Pipeline '{pipeline_id}' not found.", err=True)
        raise typer.Exit(code=1)

    import os
    from backend.orchestrator.graph import SessionInterrupt, run_session
    from backend.persistence.events import create_session as db_create_session
    from backend.pipelines.store import finish_pipeline_run, record_pipeline_run

    session_id = str(uuid.uuid4())[:8]
    workspace = os.path.expanduser(f"~/.hive/sessions/{session_id}")
    os.makedirs(workspace, exist_ok=True)

    await db_create_session(
        session_id,
        name=p["task"][:80],
        path=workspace,
        approval_mode=p["approval_mode"],
    )
    run_id = await record_pipeline_run(pipeline_id, session_id, triggered_by="cli")
    typer.echo(f"Run started: session={session_id} run={run_id}")
    typer.echo(f"Task: {p['task']}")
    typer.echo("─" * 60)

    try:
        result = await run_session(
            session_id=session_id,
            agent_id=f"worker-{session_id}",
            task=p["task"],
            model=p["model"],
            worktree_path=workspace,
            max_turns=20,
            approval_mode=p["approval_mode"],
        )
        if isinstance(result, SessionInterrupt):
            typer.echo("[paused] Approval required — resume via UI or `hive resume`.")
            return
        await finish_pipeline_run(run_id, result.get("status", "completed"))
        _print_result(result, session_id)
    except Exception as exc:
        await finish_pipeline_run(run_id, "failed")
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(code=1)


async def _pipelines_runs_async(pipeline_id: str, limit: int) -> None:
    from backend.persistence.db import init_db
    from backend.pipelines.store import get_pipeline, list_pipeline_runs

    await init_db()
    p = await get_pipeline(pipeline_id)
    if not p:
        typer.echo(f"[error] Pipeline '{pipeline_id}' not found.", err=True)
        raise typer.Exit(code=1)

    runs = await list_pipeline_runs(pipeline_id, limit=limit)
    if not runs:
        typer.echo("No runs yet.")
        return
    typer.echo(f"Runs for '{p['name']}' ({pipeline_id}):")
    typer.echo(f"{'Run ID':<14} {'Status':<11} {'Trigger':<10} {'Started':<22} Session")
    typer.echo("─" * 80)
    for r in runs:
        typer.echo(
            f"{r['id']:<14} {r['status']:<11} {r['triggered_by']:<10} "
            f"{r['started_at']:<22} {r.get('session_id') or '-'}"
        )


# ── telegram commands ────────────────────────────────────────────────────────

@telegram_app.command("setup")
def telegram_setup(
    token: str = typer.Option(..., "--token", "-t", help="Bot token from @BotFather"),
) -> None:
    """Persist the bot token to ~/.hive/telegram.json (chmod 0600)."""
    from backend.telegram.config import set_token
    config = set_token(token)
    typer.echo("Token saved.")
    if not config.allowed_chat_ids:
        typer.echo("Next: send /start from the chat you want to allow, then run:")
        typer.echo("  hive telegram allow <chat-id>")


@telegram_app.command("allow")
def telegram_allow(
    chat_id: int = typer.Argument(..., help="Telegram chat ID to allow"),
) -> None:
    """Add a chat ID to the allowlist."""
    from backend.telegram.config import add_allowed_chat
    config = add_allowed_chat(chat_id)
    typer.echo(f"Allowed chat IDs: {config.allowed_chat_ids}")


@telegram_app.command("revoke")
def telegram_revoke(
    chat_id: int = typer.Argument(..., help="Chat ID to remove"),
) -> None:
    """Remove a chat from the allowlist."""
    from backend.telegram.config import remove_allowed_chat
    config = remove_allowed_chat(chat_id)
    typer.echo(f"Allowed chat IDs: {config.allowed_chat_ids}")


@telegram_app.command("status")
def telegram_status() -> None:
    """Show current telegram config."""
    from backend.telegram.config import load_config
    config = load_config()
    typer.echo(f"Token configured: {'yes' if config.token else 'no'}")
    typer.echo(f"Allowed chats:    {config.allowed_chat_ids or '(none)'}")
    typer.echo(f"Notify approvals: {config.notify_approvals}")
    typer.echo(f"Notify end:       {config.notify_session_end}")
    typer.echo(f"Quiet hours UTC:  {config.quiet_hours or '(none)'}")


# ── start ─────────────────────────────────────────────────────────────────────

@app.command()
def start(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8765, "--port", "-p", help="Port for backend API"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev mode)"),
) -> None:
    """Start the HIVE backend API server (http://localhost:8765).

    The desktop app: cd desktop && npm run tauri:dev
    """
    try:
        import uvicorn
    except ImportError:
        typer.echo("[error] uvicorn not found. Run: uv pip install 'uvicorn[standard]'", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"HIVE backend starting on http://{host}:{port}")
    typer.echo("Desktop app: cd desktop && npm run tauri:dev")
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    app()
