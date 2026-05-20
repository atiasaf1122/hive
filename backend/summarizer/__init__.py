"""Summarizer agent — tiered reporting (Section 3 of the v1.0 plan).

A dedicated Haiku pass that runs immediately after a worker finishes
its turn (while the worker's context is still warm in VRAM, if local;
or before the API call settles, otherwise). It collapses the raw
event transcript into three tiers:

  - tldr      One sentence the user reads in the chat bubble.
  - standard  A paragraph + key decisions / open questions.
  - detailed  A full `CompletionReport` with structured evidence the
              validator stack consumes.

Workflow:
  1. Worker finishes — orchestrator collects its events.
  2. `summarize_events()` builds a transcript + asks Haiku to produce a
     JSON report at the requested tier.
  3. The validator stack runs on the detailed report; if it fails,
     orchestrator can spawn a remediation turn before the worker is
     released.
"""
from __future__ import annotations

from backend.summarizer.runner import (
    SummaryTier,
    SummarizerError,
    TieredSummary,
    summarize_events,
    summarize_transcript,
)

__all__ = [
    "SummarizerError",
    "SummaryTier",
    "TieredSummary",
    "summarize_events",
    "summarize_transcript",
]
