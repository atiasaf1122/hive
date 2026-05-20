"""Layer 3: detect quality regressions within a session.

We track the rolling validation score (Haiku cross-check, Section 5.3)
for each session. If the last 5 scores' average drops more than 15
percentage points below the overall average AND lands below 0.5, we
return a recommendation to upgrade the worker model — the orchestrator
can act on it (or surface it to the user).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean


@dataclass(frozen=True)
class AutoUpgradeRecommendation:
    session_id: str
    reason: str
    recent_average: float
    historical_average: float
    delta: float
    suggested_model: str = "claude:sonnet"


@dataclass
class QualityMonitor:
    """Rolling-window quality tracker per session."""
    session_scores: dict[str, list[float]] = field(default_factory=dict)
    window_size: int = 5
    drop_threshold: float = 0.15      # absolute drop (0-1 scale)
    floor: float = 0.5                # don't alert if absolute quality is still fine

    def record_score(
        self, session_id: str, score: float,
    ) -> AutoUpgradeRecommendation | None:
        """Record a new score (0–1). Return an upgrade rec when triggered."""
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"score must be between 0 and 1, got {score}")
        scores = self.session_scores.setdefault(session_id, [])
        scores.append(score)
        return self._evaluate(session_id)

    def _evaluate(self, session_id: str) -> AutoUpgradeRecommendation | None:
        scores = self.session_scores.get(session_id) or []
        if len(scores) < self.window_size:
            return None

        recent = scores[-self.window_size :]
        historical = scores[: -self.window_size] or recent
        recent_avg = mean(recent)
        hist_avg = mean(historical)
        delta = hist_avg - recent_avg

        if delta >= self.drop_threshold and recent_avg < self.floor:
            return AutoUpgradeRecommendation(
                session_id=session_id,
                reason=(
                    f"Recent {self.window_size}-completion quality average "
                    f"({recent_avg:.0%}) dropped {delta:.0%} below the "
                    f"session average ({hist_avg:.0%})."
                ),
                recent_average=recent_avg,
                historical_average=hist_avg,
                delta=delta,
            )
        return None

    def reset(self, session_id: str) -> None:
        self.session_scores.pop(session_id, None)


default_monitor = QualityMonitor()
