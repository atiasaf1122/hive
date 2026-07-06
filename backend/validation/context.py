"""Assemble a ValidationContext from a worker's worktree (B4).

The validators are pure (no I/O by design) — this module is the
orchestrator-side collector that inspects the worktree's git state after a
run and boils it down to `GitFileChange` rows.

Changes are measured against the branch's merge-base with the main branch,
which captures everything the agent did this run regardless of whether
HIVE's auto-commit or the agent itself committed it. Untracked/uncommitted
leftovers are added from `git status --porcelain`.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from backend.validation.validators import GitFileChange, ValidationContext

logger = logging.getLogger(__name__)


async def _git(worktree: str, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=worktree,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {worktree}")
    return out.decode(errors="replace")


async def collect_git_context(
    worktree_path: str, main_branch: str = "main"
) -> ValidationContext:
    """Build the git-change picture for one agent worktree.

    Best-effort: any git failure returns an EMPTY context, and the caller
    treats an empty context as "cannot validate" rather than "no changes"
    — validation must never fabricate failures out of collector errors.
    """
    ctx = ValidationContext(worktree_path=worktree_path)
    if not worktree_path or not Path(worktree_path).is_dir():
        return ctx

    changes: dict[str, GitFileChange] = {}
    try:
        # Committed work this run: diff against the merge-base with the
        # project's primary branch. Repos created by `git init` default to
        # 'master' unless init.defaultBranch is set, so try both — the e2e
        # dogfooding run false-negatived every claim on a master-named repo
        # when only 'main' was checked.
        # Candidate order: explicit main_branch, the conventional names,
        # then ANY other local branch that isn't a hive/ agent branch —
        # repos with nonstandard defaults ('trunk', 'develop', …) false-
        # negatived every claim (Phase D e2e finding).
        candidates: list[str] = [main_branch, "master", "main"]
        try:
            branch_list = await _git(
                worktree_path, "branch", "--format=%(refname:short)")
            candidates.extend(
                b.strip() for b in branch_list.splitlines()
                if b.strip() and not b.strip().startswith("hive/")
            )
        except RuntimeError:
            pass

        base = ""
        for candidate in dict.fromkeys(candidates):
            try:
                base = (await _git(worktree_path, "merge-base", "HEAD", candidate)).strip()
                break
            except RuntimeError:
                continue

        if base:
            name_status = await _git(worktree_path, "diff", "--name-status", f"{base}..HEAD")
            for line in name_status.splitlines():
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                status, path = parts[0].strip(), parts[-1].strip()
                changes[path] = GitFileChange(
                    path=path,
                    is_new=status.startswith("A"),
                    is_deleted=status.startswith("D"),
                )
            numstat = await _git(worktree_path, "diff", "--numstat", f"{base}..HEAD")
            for line in numstat.splitlines():
                parts = line.split("\t")
                if len(parts) != 3:
                    continue
                added, removed, path = parts
                if path in changes:
                    changes[path].lines_added = int(added) if added.isdigit() else 0
                    changes[path].lines_removed = int(removed) if removed.isdigit() else 0

        # Uncommitted leftovers (shouldn't exist after auto-commit, but an
        # agent may have .gitignored files or the commit may have failed).
        porcelain = await _git(worktree_path, "status", "--porcelain")
        for line in porcelain.splitlines():
            if len(line) < 4:
                continue
            status, path = line[:2], line[3:].strip()
            if path in changes:
                continue
            changes[path] = GitFileChange(
                path=path,
                is_new="?" in status or "A" in status,
                is_deleted="D" in status,
            )
    except Exception as exc:
        logger.warning("git context collection failed for %s: %s", worktree_path, exc)
        return ValidationContext(worktree_path=worktree_path)

    ctx.git_changes = list(changes.values())
    return ctx
