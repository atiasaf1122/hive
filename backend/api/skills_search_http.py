"""Hybrid skill-search HTTP endpoint.

    GET /api/skills/search/hybrid?q=&tag=python&tag=react&top_k=10

Returns the per-skill score breakdown so the UI can show "why this one
ranked high" hints next to each result.

When the rerank gate fires (≥5 agents, missing stack, or ambiguous
query) we route through `HaikuCaller` to refine the top-K. The caller
is session-scoped — passing `?session_id=...` enables it; without it
we keep the cheap hybrid ranking.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Query

from backend.llm.haiku import HaikuBudgetExhausted, build_caller
from backend.skills.registry import hybrid_search, maybe_rerank

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/skills")

# Skill rerank is cheaper than cross-check; cap it tighter.
_RERANK_BUDGET = int(os.environ.get("HIVE_HAIKU_RERANK_BUDGET_TOKENS", "10000"))


@router.get("/search/hybrid")
async def hybrid_search_endpoint(
    q: str = Query("", max_length=200),
    tag: list[str] = Query(default_factory=list),
    top_k: int = Query(10, ge=1, le=50),
    expected_agents: int = Query(1, ge=1, le=20),
    session_id: str | None = Query(None, max_length=128),
) -> dict:
    hits = await hybrid_search(q, tags=tag, top_k=top_k)

    caller = None
    if session_id:
        caller = build_caller(session_id, budget_tokens=_RERANK_BUDGET)

    try:
        rerank = await maybe_rerank(
            hits, query=q, tech_stack=None,
            expected_agent_count=expected_agents,
            haiku_caller=caller,
        )
    except HaikuBudgetExhausted as exc:
        logger.info("Haiku rerank budget exhausted for %s: %s", session_id, exc)
        from backend.skills.registry import RerankResult
        rerank = RerankResult(hits=hits, used_llm=False,
                              skipped_reason="budget_exhausted")

    return {
        "items": [
            {
                "id": h.skill.id,
                "name": h.skill.name,
                "description": h.skill.description,
                "tags": h.skill.tags,
                "path": h.skill.path,
                "score": {
                    "semantic": h.semantic,
                    "keyword": h.keyword,
                    "tag_match": h.tag_match,
                    "combined": h.combined,
                },
            }
            for h in rerank.hits
        ],
        "rerank_used": rerank.used_llm,
        "rerank_skipped_reason": rerank.skipped_reason,
    }
