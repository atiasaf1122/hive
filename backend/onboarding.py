"""First-run onboarding — sanity-check the host environment for HIVE.

`hive onboard` calls into this module. We:
  1. Check git is installed (>= 2.30 for worktree support)
  2. Probe backends (claude CLI, claude API, Ollama)
  3. Verify the OAuth token (if claude CLI is available)
  4. Create ~/.hive/ and initialise the SQLite DB
  5. Print next-step hints

Every step returns a `Check` so callers can render results uniformly.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from backend.detection import detect_backends
from backend.persistence.db import HIVE_DIR, ensure_hive_dir, init_db


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    hint: str = ""

    def status_icon(self) -> str:
        return "✓" if self.ok else "✗"


async def check_git() -> Check:
    """Git ≥ 2.30 required for `git worktree` flags HIVE uses."""
    git = shutil.which("git")
    if not git:
        return Check("git", False, "not found",
                    "Install git ≥ 2.30 — e.g. `sudo apt install git` on Ubuntu/WSL.")

    proc = await asyncio.create_subprocess_exec(
        "git", "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    version_line = stdout.decode().strip()  # e.g. "git version 2.43.0"
    parts = version_line.replace("git version ", "").split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1])
    except (ValueError, IndexError):
        return Check("git", True, version_line)

    ok = (major, minor) >= (2, 30)
    hint = "" if ok else "Worktree behavior in git < 2.30 is unreliable. Please upgrade."
    return Check("git", ok, version_line, hint)


async def check_backends() -> list[Check]:
    """Run backend detection and translate to Check rows."""
    status = await detect_backends()
    out: list[Check] = []
    out.append(Check(
        "claude CLI",
        status.claude_cli,
        status.claude_cli_version or "not found",
        "Install with `npm install -g @anthropic-ai/claude-code` then run `claude setup-token`.",
    ))
    out.append(Check(
        "claude API key",
        status.claude_api,
        "ANTHROPIC_API_KEY set" if status.claude_api else "not set",
        "Optional: only needed if you don't use Claude Max OAuth.",
    ))
    out.append(Check(
        "Ollama",
        status.ollama,
        ", ".join(status.ollama_models) or ("running, no models" if status.ollama else "not running"),
        "Optional: free local LLM. Start with `ollama serve`, pull a model with `ollama pull llama3.1`.",
    ))
    return out


def check_credentials_file() -> Check:
    """We don't read the token's content — we just verify the file exists and is 0600."""
    creds = HIVE_DIR / "credentials.json"
    if not creds.exists():
        return Check(
            "claude OAuth token",
            False,
            "missing",
            "Run `claude setup-token` to authenticate with your Claude Max subscription.",
        )
    mode = creds.stat().st_mode & 0o777
    if mode != 0o600:
        return Check(
            "claude OAuth token",
            True,
            f"present but mode is 0{mode:o}, expected 0600",
            f"Run `chmod 600 {creds}`.",
        )
    return Check("claude OAuth token", True, "present, 0600")


async def initialise_data_dir() -> Check:
    """Create ~/.hive and apply the SQLite schema. Idempotent."""
    ensure_hive_dir()
    try:
        await init_db()
    except Exception as exc:
        return Check("data dir", False, f"init_db failed: {exc}")
    return Check("data dir", True, str(HIVE_DIR))


async def run_onboarding() -> list[Check]:
    """Run every onboarding check in order. Returns the full list."""
    checks: list[Check] = []
    checks.append(await initialise_data_dir())
    checks.append(await check_git())
    checks.extend(await check_backends())
    checks.append(check_credentials_file())
    return checks


def render_report(checks: list[Check]) -> str:
    """Format the onboarding result as a human-readable report."""
    lines = ["HIVE Onboarding Report", "=" * 50]
    fails = 0
    for c in checks:
        line = f"  {c.status_icon()}  {c.name:<22} {c.detail}"
        lines.append(line)
        if not c.ok:
            fails += 1
            if c.hint:
                lines.append(f"      → {c.hint}")

    lines.append("=" * 50)
    if fails == 0:
        lines.append("All checks passed. You're ready to run `hive start`.")
    else:
        lines.append(f"{fails} issue(s) above need attention before running HIVE.")
    return "\n".join(lines)
