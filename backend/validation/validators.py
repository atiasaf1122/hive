"""Deterministic validators — Section 5.2.

Each validator inspects one kind of claim and returns Ok / Error
without ever consulting an LLM. They're the cheap, fast first line of
defence against hallucinated completion reports — if an agent says "I
created auth.ts" but `git status` shows no new file, this catches it
before we even pay for the Haiku cross-check.

Inputs are intentionally a `ValidationContext` dataclass the orchestrator
assembles. That keeps the validators pure (no DB or filesystem I/O
themselves) and trivial to test.
"""
from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from backend.validation.schema import CompletionReport, FileAction, FileTouched

logger = logging.getLogger(__name__)


@dataclass
class GitFileChange:
    """A row of `git status --porcelain` boiled down to what we care about."""
    path: str
    is_new: bool        # 'A' or untracked
    is_deleted: bool    # 'D'
    lines_added: int = 0
    lines_removed: int = 0


@dataclass
class CommandAuditRow:
    """Minimum we need from the command_audit table to verify a TestRun claim."""
    command: str
    exit_code: int | None


@dataclass
class ValidationContext:
    """What the validators see. Assembled by the orchestrator."""
    worktree_path: str = ""
    git_changes: list[GitFileChange] = field(default_factory=list)
    audit_rows: list[CommandAuditRow] = field(default_factory=list)
    installed_packages_after: set[str] = field(default_factory=set)


@dataclass
class ValidationFinding:
    """One validator's verdict for one claim."""
    validator: str           # the class name, e.g. "FileModificationValidator"
    ok: bool
    detail: str
    severity: str = "error"  # "error" | "warning"


@dataclass
class ValidationResult:
    """Aggregate result. `passed` is the AND of every finding being ok."""
    passed: bool
    findings: list[ValidationFinding]

    @property
    def has_critical_issues(self) -> bool:
        return any(not f.ok and f.severity == "error" for f in self.findings)


class Validator(ABC):
    @abstractmethod
    def validate(self, report: CompletionReport, ctx: ValidationContext) -> list[ValidationFinding]:
        ...


# ── concrete validators ─────────────────────────────────────────────────────


def _git_change_for(path: str, ctx: ValidationContext) -> GitFileChange | None:
    """Resolve a claim's path against the git-change set (best effort match)."""
    norm = path.lstrip("./").replace("\\", "/")
    for change in ctx.git_changes:
        if change.path.lstrip("./").replace("\\", "/") == norm:
            return change
    return None


class FileModificationValidator(Validator):
    """Compare each claimed file action against the worktree's git state."""

    def validate(self, report, ctx):
        out: list[ValidationFinding] = []
        for f in report.evidence.files_touched:
            change = _git_change_for(f.path, ctx)

            if change is None:
                # No git activity at all on this path. If the worker said
                # `created` or `modified`, that's a hallucination.
                if f.action in ("created", "modified"):
                    out.append(ValidationFinding(
                        validator=self.__class__.__name__,
                        ok=False,
                        detail=(
                            f"Worker claims to have {f.action} {f.path}, "
                            "but no matching change appears in git."
                        ),
                    ))
                # `deleted` with no git row is fine — file already wasn't tracked.
                continue

            if f.action == "created" and not change.is_new:
                out.append(ValidationFinding(
                    validator=self.__class__.__name__,
                    ok=False,
                    detail=(
                        f"Worker claims to have created {f.path}, but git shows "
                        f"it as an existing modification (not a new file)."
                    ),
                ))
            elif f.action == "deleted" and not change.is_deleted:
                out.append(ValidationFinding(
                    validator=self.__class__.__name__,
                    ok=False,
                    detail=(
                        f"Worker claims to have deleted {f.path}, but git "
                        f"doesn't show it as deleted."
                    ),
                ))
            elif f.lines_added > 0 and change.lines_added == 0 and change.lines_removed == 0:
                out.append(ValidationFinding(
                    validator=self.__class__.__name__,
                    ok=False,
                    detail=(
                        f"Worker claims to have added {f.lines_added} lines to "
                        f"{f.path}, but git diff shows no line changes."
                    ),
                    severity="warning",
                ))

        return out


class FileCreationValidator(Validator):
    """Verify each claimed `created` file exists at the path the worker named."""

    def validate(self, report, ctx):
        out: list[ValidationFinding] = []
        for f in report.evidence.files_touched:
            if f.action != "created":
                continue
            if not ctx.worktree_path:
                # No worktree to check — skip silently.
                continue
            target = os.path.join(ctx.worktree_path, f.path)
            if not os.path.exists(target):
                out.append(ValidationFinding(
                    validator=self.__class__.__name__,
                    ok=False,
                    detail=(
                        f"Worker claims to have created {f.path}, but no file "
                        f"exists at {target!r}."
                    ),
                ))
        return out


class FileDeletionValidator(Validator):
    """Verify each claimed `deleted` file is, in fact, gone."""

    def validate(self, report, ctx):
        out: list[ValidationFinding] = []
        for f in report.evidence.files_touched:
            if f.action != "deleted":
                continue
            if not ctx.worktree_path:
                continue
            target = os.path.join(ctx.worktree_path, f.path)
            if os.path.exists(target):
                out.append(ValidationFinding(
                    validator=self.__class__.__name__,
                    ok=False,
                    detail=(
                        f"Worker claims to have deleted {f.path}, but the file "
                        f"is still present at {target!r}."
                    ),
                ))
        return out


class TestRunValidator(Validator):
    """Check that every claimed test command appears in the audit log with
    the same exit code the worker reported."""

    def validate(self, report, ctx):
        out: list[ValidationFinding] = []
        # Build a quick lookup of the most recent exit_code per command.
        by_cmd: dict[str, int | None] = {}
        for row in reversed(ctx.audit_rows):
            if row.command not in by_cmd:
                by_cmd[row.command] = row.exit_code

        for t in report.evidence.tests_run:
            actual = by_cmd.get(t.command)
            if actual is None:
                out.append(ValidationFinding(
                    validator=self.__class__.__name__,
                    ok=False,
                    detail=(
                        f"Worker claims to have run `{t.command}` but the audit "
                        f"log has no record of it."
                    ),
                ))
            elif actual != t.exit_code:
                out.append(ValidationFinding(
                    validator=self.__class__.__name__,
                    ok=False,
                    detail=(
                        f"Worker reports exit_code={t.exit_code} for "
                        f"`{t.command}` but audit log shows {actual}."
                    ),
                ))
        return out


class PackageInstallValidator(Validator):
    """If the worker says it installed packages, confirm they appear in the
    post-state install set the orchestrator collected."""

    def validate(self, report, ctx):
        out: list[ValidationFinding] = []
        for pkg in report.evidence.packages_installed:
            # Workers sometimes write `react@18` or `react`. Match by prefix.
            base = pkg.split("@", 1)[0].strip().lower()
            installed = any(
                p.split("@", 1)[0].strip().lower() == base
                for p in ctx.installed_packages_after
            )
            if not installed:
                out.append(ValidationFinding(
                    validator=self.__class__.__name__,
                    ok=False,
                    detail=(
                        f"Worker claims to have installed {pkg!r}, but it's not "
                        f"in the post-run package list."
                    ),
                ))
        return out


# ── orchestrator entry point ────────────────────────────────────────────────


DEFAULT_VALIDATORS: list[Validator] = [
    FileModificationValidator(),
    FileCreationValidator(),
    FileDeletionValidator(),
    TestRunValidator(),
    PackageInstallValidator(),
]


def validate_report(
    report: CompletionReport,
    ctx: ValidationContext,
    validators: list[Validator] | None = None,
) -> ValidationResult:
    """Run every validator and collect findings.

    `passed` is True iff every finding's `ok` is True.
    `has_critical_issues` is True iff any error-severity finding failed.
    """
    if validators is None:
        validators = DEFAULT_VALIDATORS
    findings: list[ValidationFinding] = []
    for v in validators:
        try:
            findings.extend(v.validate(report, ctx))
        except Exception as exc:  # noqa: BLE001
            # A buggy validator shouldn't take down the whole check.
            logger.warning("validator %s raised: %s", type(v).__name__, exc)
            findings.append(ValidationFinding(
                validator=type(v).__name__, ok=False,
                detail=f"validator crashed: {exc}", severity="warning",
            ))
    passed = all(f.ok for f in findings)
    return ValidationResult(passed=passed, findings=findings)


async def validate_report_async(
    report: CompletionReport,
    ctx: ValidationContext,
    validators: list[Validator] | None = None,
) -> ValidationResult:
    """Async wrapper for the orchestrator's coroutine call sites."""
    return await asyncio.to_thread(validate_report, report, ctx, validators)


# ── semantic cross-check (Haiku) — interface only ────────────────────────────


@dataclass
class SemanticCheckResult:
    """0-10 score from the Haiku cross-check, plus a one-line rationale."""
    score: float
    rationale: str
    skipped: bool = False
    skipped_reason: str = ""


async def semantic_cross_check(
    report: CompletionReport,
    ctx: ValidationContext,
    *,
    haiku_caller=None,  # callable: (prompt: str) -> Awaitable[str]
) -> SemanticCheckResult:
    """Run a Haiku check on the report's `description` vs. its `evidence`.

    `haiku_caller` is injected so production wires this to `ClaudeCLIWorker`
    and tests can pass a deterministic stub. When `haiku_caller is None`
    we return `skipped=True` — that lets the orchestrator integrate the
    structure now and wire the actual LLM call in a follow-up pass.
    """
    if report.status == "failed":
        return SemanticCheckResult(
            score=10.0, rationale="status is 'failed'; no need to verify success.",
            skipped=True, skipped_reason="status_failed",
        )
    if haiku_caller is None:
        return SemanticCheckResult(
            score=0.0, rationale="Haiku caller not wired in this build.",
            skipped=True, skipped_reason="not_wired",
        )

    prompt = _build_cross_check_prompt(report, ctx)
    try:
        raw = await haiku_caller(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Haiku cross-check failed: %s", exc)
        return SemanticCheckResult(
            score=0.0, rationale=f"Haiku call failed: {exc}",
            skipped=True, skipped_reason="haiku_error",
        )

    score, rationale = _parse_haiku_response(raw)
    return SemanticCheckResult(score=score, rationale=rationale)


def _build_cross_check_prompt(report: CompletionReport, ctx: ValidationContext) -> str:
    files = "\n".join(
        f"- {f.action} {f.path} (+{f.lines_added} -{f.lines_removed})"
        for f in report.evidence.files_touched
    ) or "(none)"
    tests = "\n".join(
        f"- `{t.command}` → exit {t.exit_code}"
        for t in report.evidence.tests_run
    ) or "(none)"
    commits = ", ".join(report.evidence.git_commits) or "(none)"
    return (
        "A worker agent claims:\n\n"
        f"  {report.description.strip() or '(no description)'}\n\n"
        "Evidence collected:\n"
        f"  - git commits: {commits}\n"
        f"  - files touched:\n{files}\n"
        f"  - tests run:\n{tests}\n"
        f"  - diff summary: {report.evidence.diff_summary or '(none)'}\n\n"
        "Does the evidence support the claim? Respond with ONLY a number 0-10, "
        "where 0 = evidence contradicts the claim and 10 = evidence fully supports it. "
        "Optionally add a one-line rationale after the number."
    )


def _parse_haiku_response(raw: str) -> tuple[float, str]:
    """Pull the leading 0-10 number out of Haiku's reply."""
    text = (raw or "").strip()
    if not text:
        return 0.0, "(empty Haiku response)"
    tokens = text.split(maxsplit=1)
    try:
        score = float(tokens[0])
    except ValueError:
        return 0.0, f"Could not parse score from Haiku response: {text[:80]!r}"
    score = max(0.0, min(10.0, score))
    rationale = tokens[1].strip() if len(tokens) > 1 else "(no rationale provided)"
    return score, rationale


__all__ = [
    "CommandAuditRow", "FileCreationValidator", "FileDeletionValidator",
    "FileModificationValidator", "GitFileChange",
    "PackageInstallValidator", "SemanticCheckResult", "TestRunValidator",
    "ValidationContext", "ValidationFinding", "ValidationResult",
    "Validator", "DEFAULT_VALIDATORS",
    "semantic_cross_check", "validate_report", "validate_report_async",
]


# Used by the audit-comparing validator to keep "FileAction" type-imported.
_ = FileAction, FileTouched
