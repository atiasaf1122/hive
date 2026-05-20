"""Evidence schema for agent completion reports.

Every worker is expected to attach an `evidence` block to its
completion report (Section 3.1 of the v1.0 plan). Validators run over
the structured fields; the Haiku cross-check then reads the same
evidence and the natural-language claim and scores semantic
consistency.

We keep the schema permissive on input (workers vary in how complete
they fill it in) but strict on the field types we use.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


FileAction = Literal["created", "modified", "deleted"]
ReportStatus = Literal["done", "failed", "blocked", "needs_approval"]


class FileTouched(BaseModel):
    path: str
    action: FileAction
    lines_added: int = 0
    lines_removed: int = 0
    what_was_done: str = ""


class TestRun(BaseModel):
    # Tell pytest not to try to collect this class as a TestCase. Its name
    # starts with "Test" but it's a Pydantic data model.
    __test__ = False

    command: str
    exit_code: int
    excerpt: str = ""


class Evidence(BaseModel):
    """Structured proof a Worker attaches to its completion report."""

    git_commits: list[str] = Field(default_factory=list)
    """Short SHAs of commits the agent claims to have made."""

    files_touched: list[FileTouched] = Field(default_factory=list)
    """Files the agent claims to have created / modified / deleted."""

    tests_run: list[TestRun] = Field(default_factory=list)
    """Test commands the agent ran with their exit codes."""

    packages_installed: list[str] = Field(default_factory=list)
    """Names of packages claimed to be installed (e.g. `react@18`)."""

    diff_summary: str = ""
    """Optional plain-text rollup of the git diff (used by the Haiku check)."""

    commands_run: list[str] = Field(default_factory=list)
    """Shell commands the agent says it executed."""


class CompletionReport(BaseModel):
    status: ReportStatus
    description: str = ""
    key_decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    technical_debt: list[str] = Field(default_factory=list)
    follow_up_tasks_recommended: list[str] = Field(default_factory=list)
    evidence: Evidence = Field(default_factory=Evidence)
