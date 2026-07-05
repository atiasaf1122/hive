"""Model registry tests — current IDs only, no retired IDs anywhere in source."""
from __future__ import annotations

from pathlib import Path

from backend.models import MODEL_TIERS, resolve_cli_model

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"

# Model IDs that must never reappear in the codebase — they predate the
# central registry and the claude CLI no longer resolves them.
RETIRED_IDS = ("claude-opus-4-7", "claude-sonnet-4-6")


def test_registry_has_current_lineup() -> None:
    assert MODEL_TIERS["opus"] == "claude-opus-4-8"
    assert MODEL_TIERS["sonnet"] == "claude-sonnet-5"
    assert MODEL_TIERS["haiku"] == "claude-haiku-4-5-20251001"
    assert set(MODEL_TIERS) == {"opus", "sonnet", "haiku"}


def test_tier_aliases_pass_through() -> None:
    """The claude CLI resolves bare tier aliases itself — we must not pin."""
    for tier in MODEL_TIERS:
        assert resolve_cli_model(tier) == tier


def test_full_ids_pass_through() -> None:
    assert resolve_cli_model("claude-opus-4-8") == "claude-opus-4-8"


def test_legacy_spellings_normalised() -> None:
    assert resolve_cli_model("sonnet-4") == "sonnet"
    assert resolve_cli_model("OPUS-4") == "opus"
    assert resolve_cli_model("haiku-4") == "haiku"


def test_no_retired_model_ids_in_source() -> None:
    """Grep the backend source for retired dated IDs — fail if any linger."""
    offenders: list[str] = []
    for py in BACKEND_DIR.rglob("*.py"):
        text = py.read_text(errors="ignore")
        for retired in RETIRED_IDS:
            if retired in text:
                offenders.append(f"{py}:{retired}")
    assert not offenders, f"retired model IDs found: {offenders}"
