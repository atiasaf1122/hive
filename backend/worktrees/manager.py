"""Git worktree manager — one isolated worktree per agent.

Invariant from HIVE_BUILD_PLAN: every agent that touches files runs in its
own git worktree. No two agents share a working directory. Merging back to
the main branch is always handled by the Reviewer node.

Worktrees are created under:
    ~/.hive/worktrees/<session_id>/<agent_id>/

The target repo is the `path` field of the session (the project the user
is working on). If the project directory is not a git repo, we init one.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from backend.persistence.db import HIVE_DIR

logger = logging.getLogger(__name__)

WORKTREES_ROOT = HIVE_DIR / "worktrees"


class WorktreeManager:
    """Creates and manages git worktrees for agent isolation."""

    def __init__(self, session_id: str, project_path: str) -> None:
        self.session_id = session_id
        self.project_path = Path(project_path).resolve()
        self.session_root = WORKTREES_ROOT / session_id

    def worktree_path(self, agent_id: str) -> Path:
        return self.session_root / agent_id

    async def ensure_git_repo(self) -> None:
        """Init a git repo in the project dir if one doesn't exist.

        Self-heal: if the *global* git identity isn't set, write a local
        identity so the bootstrap commit works. Without this, missing
        ``user.name`` / ``user.email`` causes the commit to fail with
        "Author identity unknown" and the entire orchestration silently
        stalls — that was the snake-game bug in Phase 9C testing.
        """
        git_dir = self.project_path / ".git"
        if not git_dir.exists():
            logger.info("Initializing git repo in %s", self.project_path)
            await _run("git", "init", cwd=self.project_path)

        await self._ensure_local_identity()

        # Need at least one commit for worktrees to work.
        try:
            await _run("git", "rev-parse", "HEAD", cwd=self.project_path)
        except RuntimeError:
            await _run(
                "git", "commit", "--allow-empty",
                "-m", "chore: init repo for HIVE",
                cwd=self.project_path,
            )

    async def _ensure_local_identity(self) -> None:
        """If the global identity is missing, write a per-repo fallback."""
        # Check current effective identity from inside the repo.
        try:
            name = await _run("git", "config", "--get", "user.name", cwd=self.project_path)
        except RuntimeError:
            name = ""
        try:
            email = await _run("git", "config", "--get", "user.email", cwd=self.project_path)
        except RuntimeError:
            email = ""

        if name.strip() and email.strip():
            return

        logger.warning(
            "No git identity for %s — writing local fallback so worktree commits don't stall.",
            self.project_path,
        )
        await _run("git", "config", "user.name", "HIVE", cwd=self.project_path)
        await _run("git", "config", "user.email", "hive@localhost", cwd=self.project_path)

    async def create(self, agent_id: str, branch_name: str | None = None) -> Path:
        """Create a git worktree for an agent. Returns the worktree path."""
        await self.ensure_git_repo()

        wt_path = self.worktree_path(agent_id)
        wt_path.parent.mkdir(parents=True, exist_ok=True)

        branch = branch_name or f"hive/{self.session_id}/{agent_id}"

        # Remove stale worktree at this path if it exists
        if wt_path.exists():
            logger.warning("Stale worktree at %s — removing", wt_path)
            await self.remove(agent_id)

        logger.info("Creating worktree for agent=%s at %s (branch=%s)", agent_id, wt_path, branch)
        await _run(
            "git", "worktree", "add", "-b", branch, str(wt_path),
            cwd=self.project_path,
        )
        return wt_path

    async def remove(self, agent_id: str) -> None:
        """Remove a worktree and prune the reference."""
        wt_path = self.worktree_path(agent_id)
        try:
            await _run("git", "worktree", "remove", "--force", str(wt_path), cwd=self.project_path)
        except RuntimeError:
            # If the worktree command fails, fall back to manual cleanup
            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)
        try:
            await _run("git", "worktree", "prune", cwd=self.project_path)
        except RuntimeError:
            pass

    async def merge_to_main(self, agent_id: str, main_branch: str = "main") -> MergeResult:
        """Merge an agent's worktree branch back to the main branch.

        Strategy: fast-forward if possible, otherwise create a merge commit.
        On conflict: return MergeResult with conflict details for Reviewer to resolve.
        """
        branch = f"hive/{self.session_id}/{agent_id}"

        # Ensure main branch exists
        main_exists = await _branch_exists(main_branch, self.project_path)
        if not main_exists:
            main_branch = await _get_default_branch(self.project_path)

        # Check if agent branch has any commits ahead of main
        ahead = await _commits_ahead(branch, main_branch, self.project_path)
        if ahead == 0:
            logger.info("Agent %s branch has no new commits — nothing to merge", agent_id)
            return MergeResult(success=True, agent_id=agent_id, branch=branch, commits_merged=0)

        logger.info("Merging %s → %s (%d commits)", branch, main_branch, ahead)

        try:
            await _run(
                "git", "merge", "--no-ff", "-m",
                f"merge: agent {agent_id} ({self.session_id})",
                branch,
                cwd=self.project_path,
            )
            return MergeResult(success=True, agent_id=agent_id, branch=branch, commits_merged=ahead)
        except RuntimeError as exc:
            error_msg = str(exc)
            if "CONFLICT" in error_msg or "conflict" in error_msg.lower():
                # Abort the merge — Reviewer will handle resolution
                await _run("git", "merge", "--abort", cwd=self.project_path)
                conflicts = await _get_conflict_files(self.project_path)
                return MergeResult(
                    success=False,
                    agent_id=agent_id,
                    branch=branch,
                    commits_merged=0,
                    conflict_files=conflicts,
                    error=error_msg,
                )
            return MergeResult(success=False, agent_id=agent_id, branch=branch, commits_merged=0, error=error_msg)

    async def remove_session_worktrees(self) -> None:
        """Clean up all worktrees for this session."""
        if not self.session_root.exists():
            return
        for agent_dir in self.session_root.iterdir():
            if agent_dir.is_dir():
                await self.remove(agent_dir.name)
        try:
            self.session_root.rmdir()
        except OSError:
            pass


class MergeResult:
    def __init__(
        self,
        success: bool,
        agent_id: str,
        branch: str,
        commits_merged: int,
        conflict_files: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        self.success = success
        self.agent_id = agent_id
        self.branch = branch
        self.commits_merged = commits_merged
        self.conflict_files = conflict_files or []
        self.error = error

    def __repr__(self) -> str:
        return (
            f"MergeResult(success={self.success}, agent={self.agent_id}, "
            f"commits={self.commits_merged}, conflicts={self.conflict_files})"
        )


# ── git helpers ──────────────────────────────────────────────────────────────

async def _run(*args: str, cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git command failed: {' '.join(args)}\n"
            f"stderr: {stderr.decode(errors='replace').strip()}"
        )
    return stdout.decode(errors="replace").strip()


async def _branch_exists(branch: str, cwd: Path) -> bool:
    try:
        await _run("git", "rev-parse", "--verify", branch, cwd=cwd)
        return True
    except RuntimeError:
        return False


async def _get_default_branch(cwd: Path) -> str:
    try:
        result = await _run("git", "symbolic-ref", "--short", "HEAD", cwd=cwd)
        return result.strip()
    except RuntimeError:
        return "main"


async def _commits_ahead(branch: str, base: str, cwd: Path) -> int:
    try:
        result = await _run("git", "rev-list", "--count", f"{base}..{branch}", cwd=cwd)
        return int(result.strip())
    except (RuntimeError, ValueError):
        return 0


async def _get_conflict_files(cwd: Path) -> list[str]:
    try:
        result = await _run("git", "diff", "--name-only", "--diff-filter=U", cwd=cwd)
        return [f for f in result.splitlines() if f]
    except RuntimeError:
        return []
