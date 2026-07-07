"""Skill injection — builds a system-prompt addition from relevant skills."""
from __future__ import annotations

from backend.skills.registry import Skill

# Close-out cap: community SKILL.md files run up to ~90KB. Injecting them
# whole blew the Linux single-argv limit (E2BIG — the zero-event worker
# deaths) and is bad prompt hygiene regardless: a skill is guidance, not a
# second codebase. Per-skill and total budgets keep the injection bounded.
MAX_SKILL_CHARS = 4_000
MAX_CONTEXT_CHARS = 12_000


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
            body = skill.instructions
            if len(body) > MAX_SKILL_CHARS:
                body = body[:MAX_SKILL_CHARS] + "\n\n_[skill truncated for injection]_"
            parts.append(body)
        parts.append("")

    context = "\n".join(parts)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n_[skill context truncated]_"
    return context
