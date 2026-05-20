"""Approval-mode state + custom-rules persistence.

Five modes, ordered from strict to loose:

    MANUAL       — every command requires user approval
    SMART_AUTO   — ALLOWED runs auto, everything else needs OK   (default)
    FULL_AUTO    — ALLOWED + CONFIRMATION run auto, BLOCKED blocks
    BLIND_AUTO   — everything except BLOCKED runs auto
    CUSTOM_AUTO  — SMART_AUTO base + user overrides from ~/.hive/custom_policies.json

`should_execute(classification, mode, project_id=None)` is the single
gate the executor consults. Returns one of:

    "run"    — go ahead, no prompt
    "ask"    — emit `command_approval_requested` and wait
    "block"  — refuse, audit, surface to user as a hard stop
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

from backend.persistence.db import HIVE_DIR
from backend.security.command_policy import (
    CommandClassification,
    Decision,
    classify_command,
)

CUSTOM_POLICIES_FILE = HIVE_DIR / "custom_policies.json"


class ApprovalMode(StrEnum):
    MANUAL = "manual"
    SMART_AUTO = "smart_auto"
    FULL_AUTO = "full_auto"
    BLIND_AUTO = "blind_auto"
    CUSTOM_AUTO = "custom_auto"


@dataclass
class CustomPolicies:
    custom_rules: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CustomPolicies":
        return cls(custom_rules=list(data.get("custom_rules") or []))


def _resolve_path(path: Path | None) -> Path:
    """Late-bind the default so tests can monkeypatch the module constant."""
    return path or CUSTOM_POLICIES_FILE


def load_custom_policies(path: Path | None = None) -> CustomPolicies:
    """Read the persisted user rules. Missing file → empty policy."""
    target = _resolve_path(path)
    if not target.exists():
        return CustomPolicies()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupted file — back it up and start fresh so the user isn't locked out.
        backup = target.with_suffix(target.suffix + ".bak")
        try:
            backup.write_text(target.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        return CustomPolicies()
    return CustomPolicies.from_dict(data)


def save_custom_policies(policies: CustomPolicies, path: Path | None = None) -> None:
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(policies.to_dict(), indent=2), encoding="utf-8")


def should_execute(
    classification: CommandClassification,
    mode: ApprovalMode,
) -> str:
    """Return one of: 'run', 'ask', 'block'.

    Pure function on (classification, mode) — no I/O, no globals.
    """
    # BLOCKED is non-overridable. Even BLIND_AUTO doesn't unblock it.
    if classification is CommandClassification.BLOCKED:
        return "block"

    if mode is ApprovalMode.MANUAL:
        return "ask"

    if mode is ApprovalMode.SMART_AUTO or mode is ApprovalMode.CUSTOM_AUTO:
        # CUSTOM_AUTO uses SMART_AUTO logic; the customisation happens in the
        # classification step (custom_rules can flip CONFIRM → ALLOW).
        if classification is CommandClassification.ALLOWED:
            return "run"
        return "ask"

    if mode is ApprovalMode.FULL_AUTO:
        # CONFIRMATION runs auto, BLOCKED already handled above.
        return "run"

    if mode is ApprovalMode.BLIND_AUTO:
        # Same effect as FULL_AUTO in our two-non-blocked-tier scheme — but
        # the UI gate ("I accept responsibility") is what makes this mode
        # meaningfully different. Keep the function pure.
        return "run"

    # Unrecognised mode → safest default.
    return "ask"


def evaluate(
    cmd: str,
    mode: ApprovalMode,
    custom_rules: list[dict] | None = None,
) -> tuple[Decision, str]:
    """End-to-end: classify a command + decide what the executor should do.

    Returns (Decision, action) where action ∈ {'run','ask','block'}.
    """
    decision = classify_command(cmd, custom_rules=custom_rules)
    return decision, should_execute(decision.classification, mode)
