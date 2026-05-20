"""LLM helpers — thin wrappers around the Worker layer for one-shot calls.

These are NOT a replacement for the orchestrator's normal worker flow,
which produces a full event stream into the session. They exist for
ancillary single-turn calls (Haiku cross-check, Skills rerank,
Summarizer) where we just want `text in → text out` with per-session
cost accounting on top.
"""
from __future__ import annotations

from backend.llm.haiku import HaikuBudgetExhausted, HaikuCaller, build_caller

__all__ = ["HaikuBudgetExhausted", "HaikuCaller", "build_caller"]
