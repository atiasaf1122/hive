#!/usr/bin/env python3
"""HIVE PreToolUse guard — the deterministic last line (F1).

Self-contained, stdlib-only. Registered per-worker (worktree
.claude/settings.json); reads the PreToolUse JSON from stdin and checks
Bash commands ONLY against a short catastrophic list. Everything else is
allowed (exit 0, no output). This is a tripwire, not a policy engine —
the policy engine was deleted on purpose in Phase A.

Deny contract (verified against the hooks reference + live CLI): print
{"hookSpecificOutput": {"hookEventName": "PreToolUse",
 "permissionDecision": "deny", "permissionDecisionReason": ...}}
on stdout and exit 0.

--log FILE: append a JSONL line per denial so HIVE can ingest
GUARD_TRIPPED events (F1.3). Logging failures never block the deny.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

# ── catastrophic patterns ────────────────────────────────────────────────────

# Credential paths: matching the PATH is the tripwire, whatever the verb.
_CREDENTIAL_RES = [
    re.compile(r"(~|\$HOME|/home/[^/\s]+|/root)/\.ssh/", ),
    re.compile(r"(~|\$HOME|/home/[^/\s]+|/root)/\.aws/"),
    re.compile(r"(~|\$HOME|/home/[^/\s]+|/root)/\.kube/config"),
    re.compile(r"(~|\$HOME|/home/[^/\s]+|/root)/[^\s]*\.pem\b"),
    re.compile(r"(~|\$HOME|/home/[^/\s]+|/root)/\.claude/credentials"),
    re.compile(r"\.git-credentials\b"),
]

# Fork bombs: the classic self-piping function (incl. the `:` name), and
# unbounded busy loops (a real loop always has `; do`).
_FORKBOMB_RE = re.compile(r"([:\w]+)\s*\(\)\s*\{[^}]*\1\s*\|\s*\1[^}]*&[^}]*\}")
_BUSY_LOOP_RE = re.compile(r"while\s+(true|:|\[\s*1\s*\])\s*;\s*do\b")
_LOOP_BRAKE_RE = re.compile(r"\b(sleep|timeout|read|inotifywait|wait)\b")

# Device-level destruction.
_DEVICE_RES = [
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r"\bdd\b[^|;&]*\bof=/dev/"),
    re.compile(r">\s*/dev/sd[a-z]"),
]

# rm force-recursive variants: rm -rf, rm -fr, rm -r -f, rm --recursive --force…
_RM_RE = re.compile(
    r"\brm\s+(?=[^|;&]*(-[a-zA-Z]*r[a-zA-Z]*|--recursive))"
    r"(?=[^|;&]*(-[a-zA-Z]*f[a-zA-Z]*|--force))([^|;&]*)"
)
_PROTECTED_RM_PREFIXES = ("/etc", "/usr", "/var", "/boot", "/bin", "/sbin",
                          "/lib", "/opt", "/srv", "/root")


def _rm_target_verdict(args: str, worktree: str) -> str | None:
    """Reason string when any rm target is out of bounds, else None."""
    home = os.path.expanduser("~")
    for token in args.split():
        if token.startswith("-"):
            continue
        raw = token.strip("'\"")
        expanded = os.path.expanduser(
            raw.replace("$HOME", home).replace("${HOME}", home))
        if raw in ("~", "$HOME", "${HOME}") or expanded.rstrip("/") in ("/", home):
            return f"rm -rf targeting {raw!r}"
        if not expanded.startswith("/"):
            continue        # relative path → inside the worker's cwd/worktree
        norm = os.path.normpath(expanded)
        if norm.startswith(_PROTECTED_RM_PREFIXES) or norm.rstrip("/") == "/":
            return f"rm -rf targeting protected path {raw!r}"
        if norm == home or norm.startswith("/home/") and norm.count("/") == 2:
            return f"rm -rf targeting a home directory {raw!r}"
        inside_worktree = worktree and norm.startswith(os.path.normpath(worktree))
        if not inside_worktree and not norm.startswith("/tmp"):
            return f"rm -rf outside the worktree: {raw!r}"
    return None


def check_command(command: str, worktree: str) -> str | None:
    """Return a deny reason for a catastrophic command, else None."""
    for cred_re in _CREDENTIAL_RES:
        if cred_re.search(command):
            return f"credential path access: {cred_re.search(command).group(0)!r}"
    if _FORKBOMB_RE.search(command):
        return "fork bomb pattern"
    if _BUSY_LOOP_RE.search(command) and not _LOOP_BRAKE_RE.search(command):
        return "unbounded busy loop (while true without sleep/timeout)"
    for dev_re in _DEVICE_RES:
        if dev_re.search(command):
            return f"device-level destruction: {dev_re.search(command).group(0)!r}"
    for match in _RM_RE.finditer(command):
        reason = _rm_target_verdict(match.group(3), worktree)
        if reason:
            return reason
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="")
    opts = parser.parse_args()

    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — unparseable input must not block work
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not command:
        return 0

    reason = check_command(command, str(payload.get("cwd") or ""))
    if reason is None:
        return 0

    if opts.log:
        try:
            os.makedirs(os.path.dirname(opts.log), exist_ok=True)
            with open(opts.log, "a") as fh:
                fh.write(json.dumps({
                    "ts": time.time(),
                    "session_id": payload.get("session_id"),
                    "cwd": payload.get("cwd"),
                    "command": command[:500],
                    "reason": reason,
                }) + "\n")
        except OSError:
            pass  # logging must never block the deny itself

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"HIVE guard: {reason}. This command class is blocked for "
                f"all HIVE workers; accomplish the task within your worktree."
            ),
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
