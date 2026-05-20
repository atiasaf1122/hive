"""Preflight checks — run before a project is allowed to spawn agents.

The fix for the "snake game spun for 5 minutes with no error" bug
(Phase 9C testing #1): make it impossible to start a session if the
environment can't actually build anything. Today that's:

  - git is installed and `user.name` + `user.email` are configured
    (worktrees fail mysteriously without these — `Author identity unknown`)
  - claude CLI is on PATH (or the API key is set)
  - the chosen project_path exists and is writable

The endpoints:
    GET  /api/preflight/check?project_path=…
        → {ok, blockers[], warnings[], details}
    POST /api/preflight/fix-git
        body: {name, email}
        → runs `git config --global user.{name,email}` so future projects work
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from backend.detection import detect_backends

router = APIRouter(prefix="/api/preflight")


class Issue(BaseModel):
    id: str
    severity: str  # "blocker" | "warning"
    title: str
    detail: str
    fix_hint: str = ""
    auto_fixable: bool = False


class PreflightResponse(BaseModel):
    ok: bool
    blockers: list[Issue]
    warnings: list[Issue]
    git_user_name: str = ""
    git_user_email: str = ""


@router.get("/check", response_model=PreflightResponse)
async def check(project_path: str | None = None) -> PreflightResponse:
    blockers: list[Issue] = []
    warnings: list[Issue] = []

    git_name, git_email = await _git_identity()

    if not shutil.which("git"):
        blockers.append(Issue(
            id="git-missing",
            severity="blocker",
            title="git not found",
            detail="HIVE runs every agent in its own git worktree. Without git, no project can start.",
            fix_hint="Install git in WSL: sudo apt install git",
        ))
    else:
        if not git_name or not git_email:
            blockers.append(Issue(
                id="git-identity",
                severity="blocker",
                title="git author identity not set",
                detail=(
                    "Worktree commits fail with 'Author identity unknown'. "
                    "This is the most common reason agents silently stall."
                ),
                fix_hint=(
                    "git config --global user.name 'Your Name'\n"
                    "git config --global user.email 'you@example.com'"
                ),
                auto_fixable=True,
            ))

    status = await detect_backends()
    if not status.claude_cli and not status.claude_api:
        blockers.append(Issue(
            id="no-claude",
            severity="blocker",
            title="No Claude backend available",
            detail="Neither the claude CLI nor ANTHROPIC_API_KEY were detected.",
            fix_hint=(
                "Install Claude Code (npm install -g @anthropic-ai/claude-code) "
                "then authenticate with `claude setup-token`."
            ),
        ))

    if project_path:
        p = Path(os.path.expanduser(project_path))
        if not p.exists():
            warnings.append(Issue(
                id="project-path-missing",
                severity="warning",
                title=f"Workspace doesn't exist: {project_path}",
                detail="HIVE will try to create it; you can also pick another folder.",
                fix_hint="Pick a workspace that already exists from the chip menu.",
            ))
        elif not os.access(p, os.W_OK):
            blockers.append(Issue(
                id="project-path-readonly",
                severity="blocker",
                title=f"Workspace isn't writable: {project_path}",
                detail="Agents can't write files there. Pick a folder you own.",
                fix_hint="Try ~/projects or any folder under your home directory.",
            ))

    if not status.ollama:
        warnings.append(Issue(
            id="ollama-missing",
            severity="warning",
            title="Ollama not reachable",
            detail="Optional — only matters if your routing wants local models.",
            fix_hint="Start with `ollama serve`; HIVE works fine without it.",
        ))

    return PreflightResponse(
        ok=len(blockers) == 0,
        blockers=blockers,
        warnings=warnings,
        git_user_name=git_name,
        git_user_email=git_email,
    )


class GitIdentityRequest(BaseModel):
    name: str
    email: str


@router.post("/fix-git")
async def fix_git(req: GitIdentityRequest) -> dict:
    name = (req.name or "").strip()
    email = (req.email or "").strip()
    if not name or "@" not in email:
        return {"ok": False, "error": "name and a real email are required"}

    for key, value in (("user.name", name), ("user.email", email)):
        proc = await asyncio.create_subprocess_exec(
            "git", "config", "--global", key, value,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            return {"ok": False, "error": err.decode(errors="ignore")}
    return {"ok": True, "name": name, "email": email}


async def _git_identity() -> tuple[str, str]:
    """Read the global git identity. Returns ('', '') if either is unset."""
    if not shutil.which("git"):
        return ("", "")

    async def _config(key: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", "config", "--global", "--get", key,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return ""
        return out.decode().strip()

    return (await _config("user.name"), await _config("user.email"))
