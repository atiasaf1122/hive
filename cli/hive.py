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
app.add_typer(skills_app, name="skills")

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


@app.command()
def start(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8765, "--port", "-p", help="Port for backend API"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev mode)"),
) -> None:
    """Start the HIVE backend API server (http://localhost:8765).

    In a second terminal, run: cd frontend && npm run dev
    """
    try:
        import uvicorn
    except ImportError:
        typer.echo("[error] uvicorn not found. Run: uv pip install 'uvicorn[standard]'", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"HIVE backend starting on http://{host}:{port}")
    typer.echo("Frontend dev server: cd frontend && npm run dev")
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    app()
