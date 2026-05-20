"""Layer 5: heuristic stuck-state detection.

A periodic sweep over recent activity surfaces patterns that look like
the orchestrator (or a sub-agent) has gotten stuck in a loop. The
sweep is pure — it doesn't pause anything. The caller pauses on any
returned alert and shows the user a diagnostic.

Patterns detected:

  same-error          One error message repeated ≥ 5 times.
  file-thrash         One file edited ≥ 5 times in the last 10 minutes.
  no-progress         Agent is "active" but no commits or new files in 10 min.
  token-velocity      Last hour token rate > 5× the 24-hour baseline.
  reviewer-rejects    Reviewer rejecting same Builder ≥ 5 times.

Inputs are small dataclasses the caller assembles from its own state —
the detector itself doesn't read from disk or the DB, so it's trivial
to test.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum


class PatternKind(StrEnum):
    SAME_ERROR = "same_error"
    FILE_THRASH = "file_thrash"
    NO_PROGRESS = "no_progress"
    TOKEN_VELOCITY = "token_velocity"
    REVIEWER_REJECTS = "reviewer_rejects"


@dataclass(frozen=True)
class StuckPattern:
    kind: PatternKind
    detail: str
    actionable_hint: str
    severity: str = "warning"   # "warning" | "blocker"


# ── inputs ──────────────────────────────────────────────────────────────────

@dataclass
class FileEdit:
    path: str
    when: datetime
    agent_id: str


@dataclass
class ErrorEntry:
    message: str
    when: datetime


@dataclass
class ReviewerRejection:
    builder_id: str
    when: datetime


@dataclass
class ActivityWindow:
    """Snapshot of what the agent has been doing recently."""
    file_edits: list[FileEdit] = field(default_factory=list)
    errors: list[ErrorEntry] = field(default_factory=list)
    reviewer_rejections: list[ReviewerRejection] = field(default_factory=list)
    last_commit_at: datetime | None = None
    last_new_file_at: datetime | None = None
    agent_marked_active: bool = False
    last_hour_token_total: int = 0
    last_day_token_total: int = 0


# ── detector ────────────────────────────────────────────────────────────────

DEFAULT_NOW = datetime.utcnow


def detect_stuck_patterns(
    activity: ActivityWindow,
    *,
    now: datetime | None = None,
    file_edit_threshold: int = 5,
    error_repeat_threshold: int = 5,
    rejection_threshold: int = 5,
    velocity_multiplier: float = 5.0,
    file_thrash_window: timedelta = timedelta(minutes=10),
    no_progress_window: timedelta = timedelta(minutes=10),
) -> list[StuckPattern]:
    """Return every pattern that fires for this activity window."""
    when = now or DEFAULT_NOW()
    out: list[StuckPattern] = []

    # ── same-error ──
    if activity.errors:
        msg_counter = Counter(e.message for e in activity.errors)
        most_common, count = msg_counter.most_common(1)[0]
        if count >= error_repeat_threshold:
            out.append(StuckPattern(
                kind=PatternKind.SAME_ERROR,
                detail=f'"{most_common[:80]}" repeated {count} times',
                actionable_hint=(
                    "The same error keeps coming back. Either the fix isn't "
                    "addressing the root cause, or the agent is misreading "
                    "the failure. Pause and inspect the conversation."
                ),
            ))

    # ── file-thrash ──
    recent_edits = [e for e in activity.file_edits if when - e.when <= file_thrash_window]
    if recent_edits:
        file_counts = Counter(e.path for e in recent_edits)
        hot_file, hot_count = file_counts.most_common(1)[0]
        if hot_count >= file_edit_threshold:
            out.append(StuckPattern(
                kind=PatternKind.FILE_THRASH,
                detail=(
                    f"{hot_file} edited {hot_count} times in the last "
                    f"{int(file_thrash_window.total_seconds() // 60)} minutes"
                ),
                actionable_hint=(
                    "Agents are rewriting the same file repeatedly without "
                    "settling. Often this is a test that won't pass — open "
                    "the file and decide manually."
                ),
            ))

    # ── no-progress ──
    if activity.agent_marked_active:
        last_concrete = max(
            t for t in (activity.last_commit_at, activity.last_new_file_at) if t is not None
        ) if any((activity.last_commit_at, activity.last_new_file_at)) else None

        if last_concrete is None or when - last_concrete >= no_progress_window:
            out.append(StuckPattern(
                kind=PatternKind.NO_PROGRESS,
                detail=(
                    f"Agent appears active but no commits or new files for "
                    f"{int(no_progress_window.total_seconds() // 60)} minutes"
                ),
                actionable_hint=(
                    "Agent is consuming tokens with no visible output. "
                    "Likely stuck in an analysis or research loop."
                ),
            ))

    # ── token velocity ──
    if activity.last_day_token_total > 0:
        daily_hourly_avg = activity.last_day_token_total / 24
        if (
            daily_hourly_avg > 0
            and activity.last_hour_token_total / daily_hourly_avg >= velocity_multiplier
        ):
            out.append(StuckPattern(
                kind=PatternKind.TOKEN_VELOCITY,
                detail=(
                    f"Last hour ({activity.last_hour_token_total:,} tokens) "
                    f"is {activity.last_hour_token_total / daily_hourly_avg:.1f}× "
                    f"the 24-hour average ({daily_hourly_avg:,.0f}/h)"
                ),
                actionable_hint=(
                    "Token spend is well above normal. Likely a loop or a "
                    "context-bloat issue — check the live token meter."
                ),
                severity="blocker",
            ))

    # ── reviewer rejections ──
    if activity.reviewer_rejections:
        builder_counter = Counter(r.builder_id for r in activity.reviewer_rejections)
        hot_builder, reject_count = builder_counter.most_common(1)[0]
        if reject_count >= rejection_threshold:
            out.append(StuckPattern(
                kind=PatternKind.REVIEWER_REJECTS,
                detail=(
                    f"Reviewer has rejected {hot_builder}'s work {reject_count} times"
                ),
                actionable_hint=(
                    "The Reviewer keeps sending the same Builder back. Either "
                    "swap to a different Builder model or step in manually."
                ),
            ))

    return out
