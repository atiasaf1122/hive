"""Layer 1 of the safety stack: non-overridable hard limits.

These are absolute ceilings on a single autonomous run. Hitting one
of them pauses execution and surfaces the reason to the user — the
agent loop never just "keeps going" past these.

Limits are defaults; the user can tighten (but not loosen) them per
project in `Settings → Safety` (Section 6.4 UI). Loosening BLIND_AUTO
runs the same `accept-responsibility` dance as flipping the approval
mode, so it's not a silent override.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HardStops:
    """Tunable ceiling on a single autonomous run."""
    max_concurrent_agents: int = 8
    max_session_duration_hours: float = 4.0
    max_same_file_edits: int = 5
    vram_threshold_percent: int = 95
    disk_min_free_gb: float = 1.0
    max_tokens_per_autonomous_run: int = 500_000  # 2026 industry standard


DEFAULTS = HardStops()


@dataclass(frozen=True)
class HardStopViolation:
    limit_name: str
    threshold: float | int
    observed: float | int
    rationale: str
    severity: str = "blocker"  # always 'blocker' for hard stops


def check(
    *,
    concurrent_agents: int = 0,
    session_duration_hours: float = 0.0,
    same_file_edits: int = 0,
    vram_percent: float = 0.0,
    disk_free_gb: float = float("inf"),
    tokens_used: int = 0,
    limits: HardStops = DEFAULTS,
) -> HardStopViolation | None:
    """Return the *first* hard-stop violation, or None if every limit holds.

    Order matters — we surface the most actionable issue first:
        1. token budget (most common trigger, easy to extend)
        2. duration (long-running runs are how you blow weekly quotas)
        3. concurrent agents (RAM/rate-limit pressure)
        4. same-file edits (stuck loops)
        5. VRAM (Ollama-only)
        6. disk space
    """
    if tokens_used >= limits.max_tokens_per_autonomous_run:
        return HardStopViolation(
            limit_name="max_tokens_per_autonomous_run",
            threshold=limits.max_tokens_per_autonomous_run,
            observed=tokens_used,
            rationale=(
                "Autonomous run hit the token budget. "
                "Extend it explicitly to continue."
            ),
        )

    if session_duration_hours >= limits.max_session_duration_hours:
        return HardStopViolation(
            limit_name="max_session_duration_hours",
            threshold=limits.max_session_duration_hours,
            observed=session_duration_hours,
            rationale=(
                "Session ran longer than the configured ceiling. "
                "Long-running sessions are the most common way to "
                "exhaust a weekly Max quota."
            ),
        )

    if concurrent_agents >= limits.max_concurrent_agents:
        return HardStopViolation(
            limit_name="max_concurrent_agents",
            threshold=limits.max_concurrent_agents,
            observed=concurrent_agents,
            rationale=(
                "Reached the cap on agents that may run in parallel. "
                "Lower it for less rate-limit pressure, raise it for more."
            ),
        )

    if same_file_edits >= limits.max_same_file_edits:
        return HardStopViolation(
            limit_name="max_same_file_edits",
            threshold=limits.max_same_file_edits,
            observed=same_file_edits,
            rationale=(
                "One file has been edited more times than the limit allows. "
                "Usually a sign agents are thrashing — pause and inspect."
            ),
        )

    if vram_percent >= limits.vram_threshold_percent:
        return HardStopViolation(
            limit_name="vram_threshold_percent",
            threshold=limits.vram_threshold_percent,
            observed=vram_percent,
            rationale=(
                "VRAM is near saturation — loading another local model "
                "would likely crash the host."
            ),
        )

    if disk_free_gb <= limits.disk_min_free_gb:
        return HardStopViolation(
            limit_name="disk_min_free_gb",
            threshold=limits.disk_min_free_gb,
            observed=disk_free_gb,
            rationale=(
                "Disk free space is below the safety floor. "
                "Worktrees and audit logs can fill what's left fast."
            ),
        )

    return None
