"""Approval-mode gating + custom-rule persistence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.security.approval_mode import (
    ApprovalMode,
    CustomPolicies,
    evaluate,
    load_custom_policies,
    save_custom_policies,
    should_execute,
)
from backend.security.command_policy import CommandClassification


# ── should_execute ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("classification,mode,want", [
    # BLOCKED is non-overridable in every mode.
    (CommandClassification.BLOCKED, ApprovalMode.MANUAL,     "block"),
    (CommandClassification.BLOCKED, ApprovalMode.SMART_AUTO, "block"),
    (CommandClassification.BLOCKED, ApprovalMode.FULL_AUTO,  "block"),
    (CommandClassification.BLOCKED, ApprovalMode.BLIND_AUTO, "block"),
    (CommandClassification.BLOCKED, ApprovalMode.CUSTOM_AUTO, "block"),

    # MANUAL always asks.
    (CommandClassification.ALLOWED, ApprovalMode.MANUAL, "ask"),
    (CommandClassification.CONFIRMATION, ApprovalMode.MANUAL, "ask"),

    # SMART_AUTO: ALLOWED → run, CONFIRMATION → ask.
    (CommandClassification.ALLOWED,      ApprovalMode.SMART_AUTO, "run"),
    (CommandClassification.CONFIRMATION, ApprovalMode.SMART_AUTO, "ask"),

    # CUSTOM_AUTO behaves like SMART_AUTO at the gate level.
    (CommandClassification.ALLOWED,      ApprovalMode.CUSTOM_AUTO, "run"),
    (CommandClassification.CONFIRMATION, ApprovalMode.CUSTOM_AUTO, "ask"),

    # FULL_AUTO: ALLOWED + CONFIRMATION → run.
    (CommandClassification.ALLOWED,      ApprovalMode.FULL_AUTO, "run"),
    (CommandClassification.CONFIRMATION, ApprovalMode.FULL_AUTO, "run"),

    # BLIND_AUTO: same gate behaviour as FULL_AUTO (the "blind" part lives
    # in the UI's accept-responsibility checkbox, not the gate).
    (CommandClassification.ALLOWED,      ApprovalMode.BLIND_AUTO, "run"),
    (CommandClassification.CONFIRMATION, ApprovalMode.BLIND_AUTO, "run"),
])
def test_should_execute_matrix(classification, mode, want) -> None:
    assert should_execute(classification, mode) == want


# ── evaluate (classify + decide) ───────────────────────────────────────────

def test_evaluate_blocked_command() -> None:
    decision, action = evaluate("rm -rf /", ApprovalMode.FULL_AUTO)
    assert decision.classification is CommandClassification.BLOCKED
    assert action == "block"


def test_evaluate_allowed_command() -> None:
    decision, action = evaluate("git status", ApprovalMode.SMART_AUTO)
    assert decision.classification is CommandClassification.ALLOWED
    assert action == "run"


def test_evaluate_confirmation_in_smart_asks() -> None:
    decision, action = evaluate("npm install react", ApprovalMode.SMART_AUTO)
    assert decision.classification is CommandClassification.CONFIRMATION
    assert action == "ask"


def test_evaluate_confirmation_in_full_auto_runs() -> None:
    decision, action = evaluate("npm install react", ApprovalMode.FULL_AUTO)
    assert action == "run"


def test_evaluate_custom_allow_overrides_smart_auto_confirmation() -> None:
    rules = [{"pattern": r"^npm install\b", "action": "ALLOW"}]
    decision, action = evaluate(
        "npm install react", ApprovalMode.CUSTOM_AUTO, custom_rules=rules,
    )
    assert decision.classification is CommandClassification.ALLOWED
    assert decision.rule_source == "custom"
    assert action == "run"


def test_evaluate_custom_block_overrides_allowed() -> None:
    rules = [{"pattern": r"^git push\b", "action": "BLOCK"}]
    decision, action = evaluate(
        "git push origin main", ApprovalMode.FULL_AUTO, custom_rules=rules,
    )
    assert decision.classification is CommandClassification.BLOCKED
    assert action == "block"


# ── persistence ────────────────────────────────────────────────────────────

def test_load_custom_policies_missing_file(tmp_path: Path) -> None:
    target = tmp_path / "missing.json"
    policies = load_custom_policies(target)
    assert policies.custom_rules == []


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "policies.json"
    policies = CustomPolicies(custom_rules=[
        {"pattern": r"^docker\b", "action": "BLOCK"},
        {"pattern": r"^git push\b", "action": "CONFIRM"},
    ])
    save_custom_policies(policies, target)

    loaded = load_custom_policies(target)
    assert loaded.custom_rules == policies.custom_rules


def test_load_corrupted_file_backs_up_and_starts_fresh(tmp_path: Path) -> None:
    target = tmp_path / "policies.json"
    target.write_text("{ not valid json")

    policies = load_custom_policies(target)
    assert policies.custom_rules == []
    # Backup exists.
    assert (target.with_suffix(".json.bak")).exists()


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "policies.json"
    save_custom_policies(CustomPolicies(custom_rules=[]), nested)
    assert nested.exists()
    assert json.loads(nested.read_text()) == {"custom_rules": []}
