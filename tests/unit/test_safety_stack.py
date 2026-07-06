"""Safety stack — hard stops, circuit breaker, quality monitor, pattern detector.

These are pure-logic modules (no I/O, no DB), so the tests are fast and
exhaustive.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.safety.circuit_breaker import (
    BreakerRegistry,
    BreakerState,
    CircuitBreaker,
)
from backend.safety.hard_stops import DEFAULTS, HardStops, check


# ── Hard stops ──────────────────────────────────────────────────────────────

def test_hard_stops_pass_at_defaults() -> None:
    assert check() is None


def test_token_budget_is_the_first_trigger() -> None:
    v = check(tokens_used=DEFAULTS.max_tokens_per_autonomous_run)
    assert v is not None
    assert v.limit_name == "max_tokens_per_autonomous_run"
    assert "token" in v.rationale.lower()


def test_duration_trips_when_token_budget_clear() -> None:
    v = check(session_duration_hours=DEFAULTS.max_session_duration_hours + 0.1)
    assert v is not None
    assert v.limit_name == "max_session_duration_hours"


def test_same_file_edits_trips() -> None:
    v = check(same_file_edits=DEFAULTS.max_same_file_edits)
    assert v is not None
    assert v.limit_name == "max_same_file_edits"


def test_vram_threshold_trips() -> None:
    v = check(vram_percent=99)
    assert v is not None
    assert v.limit_name == "vram_threshold_percent"


def test_disk_threshold_trips() -> None:
    v = check(disk_free_gb=0.5)
    assert v is not None
    assert v.limit_name == "disk_min_free_gb"


def test_tighter_user_limits_apply() -> None:
    user_limits = HardStops(max_concurrent_agents=2)
    assert check(concurrent_agents=2, limits=user_limits) is not None
    assert check(concurrent_agents=1, limits=user_limits) is None


# ── Circuit breaker ─────────────────────────────────────────────────────────

class FakeClock:
    """Lets us drive `datetime.utcnow` in the breaker without sleeping."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


def make_breaker(now: datetime) -> tuple[CircuitBreaker, FakeClock]:
    clock = FakeClock(now)
    cb = CircuitBreaker(worker_id="builder-sonnet",
                        failure_threshold=3,
                        cool_down=timedelta(minutes=5))
    cb._now = lambda: clock.now  # type: ignore[assignment]
    return cb, clock


def test_breaker_starts_closed() -> None:
    cb, _ = make_breaker(datetime(2026, 5, 20, 10, 0))
    assert cb.state is BreakerState.CLOSED
    assert cb.can_attempt() is True


def test_breaker_trips_after_threshold_failures() -> None:
    cb, _ = make_breaker(datetime(2026, 5, 20, 10, 0))
    cb.record_failure()
    cb.record_failure()
    assert cb.state is BreakerState.CLOSED  # not yet
    cb.record_failure()
    assert cb.state is BreakerState.OPEN
    assert cb.total_trips == 1
    assert cb.can_attempt() is False


def test_success_resets_consecutive_failures() -> None:
    cb, _ = make_breaker(datetime(2026, 5, 20, 10, 0))
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    # Two failures after the success — still under threshold.
    assert cb.state is BreakerState.CLOSED


def test_breaker_half_opens_after_cool_down() -> None:
    cb, clock = make_breaker(datetime(2026, 5, 20, 10, 0))
    for _ in range(3):
        cb.record_failure()
    assert cb.state is BreakerState.OPEN

    clock.advance(timedelta(minutes=5, seconds=1))
    # can_attempt() flips us to HALF_OPEN as a side effect.
    assert cb.can_attempt() is True
    assert cb.state is BreakerState.HALF_OPEN


def test_half_open_success_closes_the_breaker() -> None:
    cb, clock = make_breaker(datetime(2026, 5, 20, 10, 0))
    for _ in range(3):
        cb.record_failure()
    clock.advance(timedelta(minutes=5, seconds=1))
    cb.can_attempt()
    cb.record_success()
    assert cb.state is BreakerState.CLOSED
    assert cb.consecutive_failures == 0


def test_half_open_failure_re_opens_with_fresh_cooldown() -> None:
    cb, clock = make_breaker(datetime(2026, 5, 20, 10, 0))
    for _ in range(3):
        cb.record_failure()
    clock.advance(timedelta(minutes=5, seconds=1))
    cb.can_attempt()
    cb.record_failure()
    assert cb.state is BreakerState.OPEN
    assert cb.total_trips == 2
    assert cb.time_until_close() > 0


def test_registry_returns_same_breaker_per_id() -> None:
    reg = BreakerRegistry()
    a = reg.get("builder-sonnet")
    b = reg.get("builder-sonnet")
    assert a is b
    snap = reg.snapshot()
    assert len(snap) == 1
    assert snap[0]["worker_id"] == "builder-sonnet"


def test_registry_reset_zeroes_a_breaker() -> None:
    reg = BreakerRegistry()
    cb = reg.get("worker-x")
    for _ in range(3):
        cb.record_failure()
    assert cb.state is BreakerState.OPEN
    reg.reset("worker-x")
    assert cb.state is BreakerState.CLOSED
    assert cb.consecutive_failures == 0
