"""Curated catalog of MCP servers HIVE knows how to launch (Phase C).

Code, not DB — it changes rarely and git is its version history. This is
NOT the Phase 9C registries browser (that stays for discovery); this is
the small, runnable set the planner can assign to agents.

Placeholders expanded at render time:
  {agent_id}   — the spawning agent's id
  {worktree}   — the agent's worktree path
  {tmpdir}     — the system temp dir

Env references in `env` / `headers` values:
  "${VAR}"           — required; render fails if missing (preflight catches it first)
  "${VAR:optional}"  — dropped silently when the variable is unset
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

# Matches ${VAR} / ${VAR:optional} anywhere inside a value (e.g. the
# GitHub header "Bearer ${GITHUB_TOKEN}" embeds the reference mid-string).
_ENV_RE = re.compile(r"\$\{(?P<name>[A-Z0-9_]+)(?P<opt>:optional)?\}")


@dataclass(frozen=True)
class MCPServerSpec:
    id: str
    label: str
    command: str = ""                    # stdio transport
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    per_agent_isolation: bool = False    # True => isolation_args are mandatory
    isolation_args: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)  # "node>=20" | "env:VAR"
    notes: str = ""
    transport: str = "stdio"             # "stdio" | "http"
    url: str = ""                        # http transport
    headers: dict[str, str] = field(default_factory=dict)
    # One-line planner guidance — rendered into the planner prompt digest.
    when_to_use: str = ""


CATALOG: dict[str, MCPServerSpec] = {
    spec.id: spec
    for spec in [
        MCPServerSpec(
            id="playwright",
            label="Playwright (browser + vision)",
            command="npx",
            # --isolated: in-memory profile per instance; --headless: never
            # pop windows on the host. Parallel agents sharing a browser
            # profile corrupt each other's sessions — isolation is
            # non-negotiable, hence per_agent_isolation=True.
            args=["-y", "@playwright/mcp@latest", "--headless", "--isolated"],
            per_agent_isolation=True,
            isolation_args=[
                "--user-data-dir", "{tmpdir}/hive-pw-{agent_id}",
                "--output-dir", "{worktree}/.playwright",
            ],
            tags=["browser", "vision", "screenshots", "e2e-testing", "scraping"],
            requires=["node>=20"],
            notes=(
                "Real browser control + screenshots. First use downloads "
                "browsers (~700MB) — expect a slow first run."
            ),
            when_to_use=(
                "ONLY for subtasks that verify UI in a real browser, e2e-test "
                "a running web app, or scrape pages. NOT for writing frontend "
                "code. Browser agents need claude:sonnet minimum (never haiku)."
            ),
        ),
        MCPServerSpec(
            id="github",
            label="GitHub (issues, PRs, CI)",
            transport="http",
            # The npm server-github package is DEPRECATED; the official
            # server is a Go binary/Docker image or this hosted remote.
            # Remote is the simplest launch from HIVE: zero local installs,
            # auth via the PAT already used for git push.
            url="https://api.githubcopilot.com/mcp/",
            headers={"Authorization": "Bearer ${GITHUB_TOKEN}"},
            tags=["github", "repo", "issues", "prs", "ci"],
            requires=["env:GITHUB_TOKEN"],
            notes="Hosted GitHub MCP — needs GITHUB_TOKEN (PAT).",
            when_to_use=(
                "When the subtask reads/creates GitHub issues or PRs, or "
                "inspects CI runs. NOT for local git operations (built in)."
            ),
        ),
        MCPServerSpec(
            id="context7",
            label="Context7 (live library docs)",
            command="npx",
            args=["-y", "@upstash/context7-mcp"],
            env={"CONTEXT7_API_KEY": "${CONTEXT7_API_KEY:optional}"},
            tags=["docs", "libraries", "api-reference"],
            requires=["node>=20"],
            notes="Up-to-date library docs; free tier needs no key.",
            when_to_use=(
                "When working against a fast-moving library where stale "
                "training data is a real risk. Otherwise skip."
            ),
        ),
        MCPServerSpec(
            id="filesystem",
            label="Filesystem (cross-project reads)",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem"],
            per_agent_isolation=True,
            # The allowed-dirs positional args come LAST. Default scope is
            # the agent's own worktree — never / or ~. Widening to a
            # specific extra dir is a planner decision, visible in approval.
            isolation_args=["{worktree}"],
            tags=["files", "cross-project"],
            requires=["node>=20"],
            notes=(
                "RARELY needed — workers already have Read/Write/Glob in "
                "their cwd. Only for explicit cross-project access."
            ),
            when_to_use=(
                "RARELY — only for explicit cross-project file access, with "
                "the extra directory named in the subtask."
            ),
        ),
    ]
}


def get_spec(server_id: str) -> MCPServerSpec | None:
    return CATALOG.get(server_id)


def list_specs() -> list[MCPServerSpec]:
    return list(CATALOG.values())


# ── preflight ───────────────────────────────────────────────────────────────


def _node_major() -> int | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return int(out.lstrip("v").split(".")[0])
    except Exception:  # noqa: BLE001
        return None


def preflight(spec: MCPServerSpec) -> list[str]:
    """Return human-readable missing requirements (empty list = ready)."""
    missing: list[str] = []
    for req in spec.requires:
        if req.startswith("env:"):
            var = req.split(":", 1)[1]
            if not os.environ.get(var):
                missing.append(f"environment variable {var} is not set")
        elif req.startswith("node>="):
            want = int(req.split(">=")[1])
            have = _node_major()
            if have is None:
                missing.append("node is not installed")
            elif have < want:
                missing.append(f"node>={want} required (found v{have})")
        else:
            missing.append(f"unknown requirement {req!r}")
    return missing


# ── rendering to the claude CLI's mcpServers shape ──────────────────────────


def _expand_placeholders(value: str, agent_id: str, worktree: str) -> str:
    return (
        value.replace("{agent_id}", agent_id)
        .replace("{worktree}", worktree)
        .replace("{tmpdir}", tempfile.gettempdir())
    )


def _expand_env_map(raw: dict[str, str]) -> dict[str, str]:
    """Resolve ${VAR} / ${VAR:optional} references against os.environ.

    References may be embedded mid-string ("Bearer ${GITHUB_TOKEN}"). A
    missing required var raises; an entry whose optional var is unset is
    dropped from the result entirely.
    """
    out: dict[str, str] = {}
    for key, value in raw.items():
        drop = False

        def _sub(m: re.Match) -> str:
            nonlocal drop
            resolved = os.environ.get(m.group("name"))
            if resolved:
                return resolved
            if m.group("opt"):
                drop = True
                return ""
            raise ValueError(
                f"required environment variable {m.group('name')} is not set"
            )

        expanded = _ENV_RE.sub(_sub, value)
        if not drop:
            out[key] = expanded
    return out


def render_mcp_config(
    server_ids: list[str], agent_id: str, worktree: str
) -> dict:
    """Build the `{"mcpServers": {...}}` JSON the claude CLI consumes.

    Unknown ids raise — callers validate at plan-parse time, so reaching
    here with a bad id is a bug, not user input.
    """
    servers: dict[str, dict] = {}
    for sid in server_ids:
        spec = get_spec(sid)
        if spec is None:
            raise ValueError(f"unknown MCP server id {sid!r}")

        if spec.transport == "http":
            servers[sid] = {
                "type": "http",
                "url": _expand_placeholders(spec.url, agent_id, worktree),
                "headers": _expand_env_map(spec.headers),
            }
            continue

        args = [
            _expand_placeholders(a, agent_id, worktree)
            for a in (*spec.args, *spec.isolation_args)
        ]
        entry: dict = {"command": spec.command, "args": args}
        env = _expand_env_map(spec.env)
        if env:
            entry["env"] = env
        servers[sid] = entry
    return {"mcpServers": servers}
