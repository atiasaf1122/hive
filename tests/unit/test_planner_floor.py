"""Planner floor: an orchestrator-decided team must never have zero active agents.

If the LLM returns an empty team (or only passive members) for a coding ask,
`_parse_team_composition` inserts a default Builder so the spawn stage can
always proceed.
"""
from __future__ import annotations

import json

from backend.orchestrator.nodes.planner import _parse_team_composition


def test_planner_inserts_builder_when_team_is_empty() -> None:
    raw = json.dumps({"team": [], "confidence": 0.9, "rationale": "nothing"})
    comp = _parse_team_composition(raw)
    assert comp.total_active >= 1
    assert comp.team[0].role == "Builder"
    assert comp.team[0].passive is False


def test_planner_inserts_builder_when_all_members_passive() -> None:
    raw = json.dumps({
        "team": [
            {"role": "Debugger", "model": "claude:sonnet", "count": 1, "passive": True},
        ],
        "confidence": 0.8,
        "rationale": "just observe",
    })
    comp = _parse_team_composition(raw)
    assert comp.total_active >= 1
    builders = [m for m in comp.team if m.role == "Builder" and not m.passive]
    assert len(builders) == 1


def test_planner_leaves_valid_team_unchanged() -> None:
    raw = json.dumps({
        "team": [
            {"role": "Builder", "model": "claude:sonnet", "count": 2, "passive": False},
        ],
        "confidence": 0.9,
        "rationale": "good team",
    })
    comp = _parse_team_composition(raw)
    # B1: count=2 expands to two single-agent members.
    assert len(comp.team) == 2
    assert all(m.count == 1 for m in comp.team)
    assert comp.rationale == "good team"
