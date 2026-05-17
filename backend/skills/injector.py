"""Skill injection — builds a system-prompt addition from relevant skills."""
from __future__ import annotations

from backend.skills.registry import Skill


def build_skill_context(skills: list[Skill]) -> str:
    """Return a markdown string suitable for prepending to an agent's system prompt."""
    if not skills:
        return ""

    parts = ["## Relevant Skills\n"]
    for skill in skills:
        parts.append(f"### {skill.name}")
        if skill.description:
            parts.append(f"_{skill.description}_\n")
        if skill.instructions:
            parts.append(skill.instructions)
        parts.append("")

    return "\n".join(parts)
