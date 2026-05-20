"""Layer 2: per-worker circuit breaker.

Classic three-state machine:

    CLOSED      — normal operation; failures count toward the trip threshold
    OPEN        — the breaker just tripped; reject attempts until cool-down
    HALF_OPEN   — cool-down elapsed; allow ONE probe; success → CLOSED,
                  failure → back to OPEN with the clock reset

We trip after 3 consecutive failures (default) and stay OPEN for 5 minutes.
Use one instance per worker id (e.g. `"builder-sonnet"`, `"tester-llama-13b"`).
The registry below is process-local and survives only as long as the
backend runs — Phase 10 fix-up will persist these on shutdown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    worker_id: str
    failure_threshold: int = 3
    cool_down: timedelta = timedelta(minutes=5)
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    opened_at: datetime | None = None
    total_trips: int = 0

    def _now(self) -> datetime:
        # Indirection so tests can freeze time.
        return datetime.now(timezone.utc)

    def can_attempt(self) -> bool:
        """Should the next attempt be allowed?"""
        if self.state is BreakerState.CLOSED:
            return True
        if self.state is BreakerState.OPEN:
            assert self.opened_at is not None
            if self._now() - self.opened_at >= self.cool_down:
                # Move to HALF_OPEN — the *next* call's success/failure
                # decides whether we close fully or trip again.
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        # HALF_OPEN — allow exactly one probe; record_failure / record_success
        # is responsible for transitioning out.
        return True

    def record_success(self) -> None:
        if self.state in (BreakerState.HALF_OPEN, BreakerState.CLOSED):
            self.state = BreakerState.CLOSED
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.state is BreakerState.HALF_OPEN:
            # Probe failed — straight back to OPEN with fresh cool-down.
            self.state = BreakerState.OPEN
            self.opened_at = self._now()
            self.total_trips += 1
            return
        if self.consecutive_failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = self._now()
            self.total_trips += 1

    def time_until_close(self) -> float:
        """Seconds left in the cool-down. 0 when CLOSED/HALF_OPEN."""
        if self.state is not BreakerState.OPEN or self.opened_at is None:
            return 0.0
        elapsed = self._now() - self.opened_at
        remaining = (self.cool_down - elapsed).total_seconds()
        return max(0.0, remaining)


@dataclass
class BreakerRegistry:
    """Process-local collection of per-worker breakers."""
    _store: dict[str, CircuitBreaker] = field(default_factory=dict)

    def get(self, worker_id: str) -> CircuitBreaker:
        cb = self._store.get(worker_id)
        if cb is None:
            cb = CircuitBreaker(worker_id=worker_id)
            self._store[worker_id] = cb
        return cb

    def snapshot(self) -> list[dict]:
        return [
            {
                "worker_id": cb.worker_id,
                "state": cb.state,
                "consecutive_failures": cb.consecutive_failures,
                "time_until_close_seconds": cb.time_until_close(),
                "total_trips": cb.total_trips,
            }
            for cb in self._store.values()
        ]

    def reset(self, worker_id: str) -> None:
        """Manual reset from the UI."""
        if worker_id in self._store:
            self._store[worker_id].state = BreakerState.CLOSED
            self._store[worker_id].consecutive_failures = 0
            self._store[worker_id].opened_at = None


# Global default registry — wired in to the orchestrator's failure path.
default_registry = BreakerRegistry()
