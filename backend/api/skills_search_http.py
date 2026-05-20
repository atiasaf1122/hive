"""Hybrid skill-search HTTP endpoint.

    GET /api/skills/search/hybrid?q=&tag=python&tag=react&top_k=10

Returns the per-skill score breakdown so the UI can show "why this one
ranked high" hints next to each result.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from backend.skills.registry import hybrid_search, maybe_rerank

router = APIRouter(prefix="/api/skills")


@router.get("/search/hybrid")
async def hybrid_search_endpoint(
    q: str = Query("", max_length=200),
    tag: list[str] = Query(default_factory=list),
    top_k: int = Query(10, ge=1, le=50),
    expected_agents: int = Query(1, ge=1, le=20),
) -> dict:
    hits = await hybrid_search(q, tags=tag, top_k=top_k)
    rerank = await maybe_rerank(
        hits, query=q, tech_stack=None,
        expected_agent_count=expected_agents,
        haiku_caller=None,  # wiring in a follow-up pass; structure ready
    )
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
