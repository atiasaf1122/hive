"""Central model registry — the single source of truth for model selection.

HIVE model strings have the shape "<backend>:<tier-or-name>", e.g.
"claude:sonnet" or "ollama:llama3.1". For the claude backend, the tier part
is passed straight to `claude --model`: the CLI resolves tier aliases
('opus', 'sonnet', 'haiku') to the latest model itself, so HIVE never pins
dated IDs and keeps working across Anthropic model bumps.

MODEL_TIERS documents the current lineup those aliases resolve to. It is
for display and validation only — do NOT use it to pin `--model` values.
"""
from __future__ import annotations

# Current lineup (July 2026). Update when Anthropic ships new tiers.
MODEL_TIERS: dict[str, str] = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
}

# Backend-wide defaults. "sonnet" is the workhorse tier (cost discipline:
# strong models only where the task warrants it — see CLAUDE.md invariant 7).
DEFAULT_MODEL = "claude:sonnet"
HAIKU_MODEL = "claude:haiku"

# Retired shorthand spellings from the pre-registry map. The bare tier
# alias is what the claude CLI understands.
_LEGACY_ALIASES: dict[str, str] = {
    "opus-4": "opus",
    "sonnet-4": "sonnet",
    "haiku-4": "haiku",
}


def resolve_cli_model(shorthand: str) -> str:
    """Return the value to pass to `claude --model`.

    Tier aliases and full model IDs both pass through unchanged (the CLI
    accepts either); only retired legacy spellings are normalised.
    """
    return _LEGACY_ALIASES.get(shorthand.lower(), shorthand)
