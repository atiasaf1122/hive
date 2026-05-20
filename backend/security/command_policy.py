"""Command classification policy.

Every shell command an agent wants to run is classified into one of:

    BLOCKED       — never runs, even in BLIND_AUTO. Hard stop.
    ALLOWED       — runs without prompting in SMART_AUTO and above.
    CONFIRMATION  — requires a user OK in SMART_AUTO; runs auto in FULL_AUTO.

Patterns are case-insensitive and applied to a whitespace-normalised form
of the command. The classifier evaluates lists in this order:

    1. BLOCKED       (non-overridable — checked first)
    2. ALWAYS_ALLOWED (cheap / read-only / sandbox-safe)
    3. REQUIRES_CONFIRMATION (writes, installs, network, dev servers, etc.)
    4. default       → CONFIRMATION (safe default: ask the user)

Custom user rules layer on top of this — see `approval_mode.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class CommandClassification(StrEnum):
    BLOCKED = "blocked"
    ALLOWED = "allowed"
    CONFIRMATION = "confirmation"


@dataclass(frozen=True)
class PolicyRule:
    pattern: re.Pattern[str]
    label: str
    rationale: str


# ─────────────────────────────────────────────────────────────────────────────
# ALWAYS_BLOCKED — never run, even in BLIND_AUTO.
# ─────────────────────────────────────────────────────────────────────────────

ALWAYS_BLOCKED_PATTERNS: list[tuple[str, str, str]] = [
    # Destructive recursive deletes anchored at filesystem roots / globs.
    (r"^rm\s+-rf?\s+/(\s|$)", "rm-rf-root",
     "rm -rf with `/` as the target wipes the filesystem."),
    (r"^rm\s+-rf?\s+/\*", "rm-rf-root-glob",
     "rm -rf /* targets every top-level directory."),
    (r"^rm\s+-rf?\s+~(\s|/?$)", "rm-rf-home",
     "rm -rf with `~` as the target wipes the user's home directory."),
    (r"^rm\s+-rf?\s+~/\*", "rm-rf-home-glob",
     "rm -rf ~/* wipes everything in the user's home."),
    (r"^rm\s+-rf?\s+[a-z]:\\?\s*$", "rm-rf-windows-drive",
     "rm -rf on a Windows drive root wipes the drive."),
    (r"^rm\s+-rf?\s+\$home(\s|/?$)", "rm-rf-home-var",
     "Same effect as ~/ — wipes home."),
    (r"^del\s+/[sf]\s+/q?\s+[a-z]:\\\\?\s*$", "del-windows-drive",
     "del /s on a Windows drive root."),

    # Privilege escalation.
    (r"^sudo(\s|$)", "sudo",
     "Privilege escalation is out of scope for an agent."),
    (r"^doas(\s|$)", "doas", "Privilege escalation."),
    (r"^su\s+-?\s*$", "su", "Switch user."),
    (r"^chmod\s+(-r\s+)?777(\s|$)", "chmod-777",
     "Granting world-write on everything is almost never correct."),
    (r"^chown\s+(-r\s+)?root", "chown-root",
     "Chown to root requires escalation; not safe to attempt."),

    # Raw disk operations.
    (r"^dd\s+if=", "dd-if",
     "dd can destroy entire partitions."),
    (r"^mkfs(\.|\s)", "mkfs", "Formatting a filesystem."),
    (r"^format\s+[a-z]:", "format-windows",
     "Formatting a Windows volume."),
    (r"^(fdisk|parted|gparted)(\s|$)", "partition-tool",
     "Partition table changes."),

    # Network attack / listening tools.
    (r"^nmap(\s|$)", "nmap", "Port-scanning hosts."),
    (r"^nc\s+-l\s+\d+", "nc-listen",
     "Opening a listening socket — not an agent task."),
    (r"^(iptables|ufw)(\s|$)", "firewall",
     "Modifying the host firewall."),

    # System service / cron modification.
    (r"^crontab\s+-e", "crontab-e",
     "Editing the user's crontab; outside the project scope."),
    (r"^(systemctl|service)\s+", "systemctl",
     "Managing system services."),

    # Writing into protected system directories.
    (r">\s*/?etc/", "write-etc",
     "Writing to /etc/ requires root and changes system config."),
    (r">\s*(/|\\)windows(/|\\)", "write-windows",
     "Writing into C:\\Windows\\."),

    # Credential theft patterns.
    (r"\b(cat|head|tail|less|more|type)\s+.*\.ssh/id_(rsa|ed25519|ecdsa|dsa)",
     "read-ssh-key", "Reading an SSH private key."),
    (r"\b(cat|head|tail|less|more|type)\s+.*\.aws/credentials",
     "read-aws-creds", "Reading AWS credentials."),
    (r"\b(cat|head|tail|less|more|type)\s+.*\.kube/config",
     "read-kube-config", "Reading kubeconfig."),
    (r"\b(cat|head|tail|less|more|type)\s+.*\.netrc",
     "read-netrc", ".netrc contains plaintext credentials."),
    (r"\b(cat|head|tail|less|more|type)\s+.*%appdata%.*credentials",
     "read-appdata-creds", "Reading Windows credential store."),

    # Fork bombs.
    (r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork-bomb",
     "Classic bash fork bomb."),
    (r"^while\s+true.*do.*done\s*$", "while-true-no-timeout",
     "Unbounded busy loop without timeout."),
]


# ─────────────────────────────────────────────────────────────────────────────
# ALWAYS_ALLOWED — cheap, read-only, sandbox-safe operations.
# ─────────────────────────────────────────────────────────────────────────────

ALWAYS_ALLOWED_PATTERNS: list[tuple[str, str]] = [
    # Git reads.
    (r"^git\s+(status|log|diff|show|blame|branch|ls-files|rev-parse|remote\s+-v)\b",
     "git-read"),
    (r"^git\s+config\s+(--get|--list)\b", "git-config-read"),

    # Git safe writes (we assume the agent is operating in its worktree).
    (r"^git\s+add\b", "git-add"),
    (r"^git\s+commit\b", "git-commit"),
    (r"^git\s+stash\b", "git-stash"),
    (r"^git\s+checkout\s+-b\b", "git-new-branch"),
    (r"^git\s+switch\s+-c\b", "git-new-branch-modern"),

    # Generic reads.
    (r"^(ls|dir|cat|head|tail|less|wc|tree|du|stat|file)\b", "read-cmd"),
    (r"^(find|grep|rg|ag|ack)\b", "search-cmd"),

    # Process info (read only).
    (r"^ps(\s|$)", "ps"),
    (r"^top\s+-n\s+1\b", "top-snapshot"),
    (r"^(whoami|pwd|id|date|hostname)(\s|$)", "info-cmd"),
    (r"^(which|where|type)\s+\S+\s*$", "which-cmd"),
    (r"^env(\s|$)", "env"),

    # Version queries — common tools' `--version` / `-V` flag.
    (r"^(node|python|python3|pip|uv|cargo|rustc|npm|yarn|pnpm|go|bun|git|docker|kubectl|tauri)"
     r"\s+--?version\b", "version-query"),
    (r"^(node|python|python3|cargo|rustc)\s+-V\b", "version-query"),

    # Package listings (read-only).
    (r"^npm\s+(list|ls|outdated|view|why|root)\b", "npm-read"),
    (r"^(yarn|pnpm)\s+list\b", "yarn-list"),
    (r"^pip\s+(list|show|check)\b", "pip-read"),
    (r"^cargo\s+(tree|metadata)\b", "cargo-read"),
    (r"^go\s+list\b", "go-list"),

    # Testing — running tests.
    (r"^npm\s+(test|t|run\s+test|run\s+test:\S+)\b", "npm-test"),
    (r"^(yarn|pnpm)\s+(test|run\s+test)\b", "yarn-test"),
    (r"^pytest(\s|$)", "pytest"),
    (r"^python\s+-m\s+pytest\b", "pytest-mod"),
    (r"^cargo\s+(test|check|clippy)\b", "cargo-test"),
    (r"^go\s+test\b", "go-test"),
    (r"^(jest|vitest|mocha|playwright\s+test|cypress\s+run)\b", "node-test"),

    # Linters (no-fix variants).
    (r"^eslint\s+(?!.*--fix\b).*$", "eslint-check"),
    (r"^prettier\s+--check\b", "prettier-check"),
    (r"^ruff\s+check\b(?!.*--fix)", "ruff-check"),
    (r"^black\s+--check\b", "black-check"),
    (r"^mypy\b", "mypy"),
    (r"^(rustfmt|cargo\s+fmt)\s+--check\b", "rust-fmt-check"),
    (r"^pyright\b", "pyright"),

    # Build (no install / no network side effects).
    (r"^npm\s+run\s+(build|compile|typecheck|lint)\b", "npm-build"),
    (r"^(yarn|pnpm)\s+(build|compile)\b", "yarn-build"),
    (r"^tsc(\s+(-b|--noemit|--build|--watch\s+-w))?\b", "tsc-build"),
    (r"^cargo\s+build(\s+--release)?\b", "cargo-build"),
    (r"^vite\s+build\b", "vite-build"),
    (r"^webpack(\s+--mode=\w+)?\b", "webpack"),
    (r"^next\s+build\b", "next-build"),
    (r"^go\s+build\b", "go-build"),
]


# ─────────────────────────────────────────────────────────────────────────────
# REQUIRES_CONFIRMATION — writes, installs, network, dev servers, db ops.
# ─────────────────────────────────────────────────────────────────────────────

REQUIRES_CONFIRMATION_PATTERNS: list[tuple[str, str]] = [
    # Package installs (real network-side-effecting commands).
    (r"^npm\s+(install|i|add|update)\b", "npm-install"),
    (r"^(yarn|pnpm)\s+(add|install|upgrade)\b", "yarn-install"),
    (r"^pip\s+(install|uninstall|upgrade)\b", "pip-install"),
    (r"^uv\s+pip\s+(install|sync|uninstall)\b", "uv-install"),
    (r"^uv\s+add\b", "uv-add"),
    (r"^cargo\s+(add|install|remove)\b", "cargo-install"),
    (r"^(gem|brew|apt|apt-get|dnf|pacman|yum)\s+(install|upgrade|remove)\b",
     "sys-pkg-install"),

    # Code execution of an explicit file (could do anything).
    (r"^node\s+\S+\.(js|mjs|cjs)\b", "node-run-file"),
    (r"^python3?\s+\S+\.py\b", "python-run-file"),
    (r"^(bash|sh|zsh|fish)\s+\S+\.(sh|bash|zsh)\b", "shell-run-file"),
    (r"^deno\s+run\b", "deno-run"),
    (r"^bun\s+\S+\.(ts|js)\b", "bun-run-file"),
    (r"\beval\s+", "eval"),
    (r"\bexec\s+", "exec"),

    # Classic pipe-to-shell attack.
    (r"(curl|wget|fetch)\s+[^|]*\|\s*(bash|sh|zsh|fish)\b", "pipe-to-shell"),

    # Network downloads + clones.
    (r"^curl\s+(-[a-z]*o\b|-o\b|.*--output\b)", "curl-download"),
    (r"^wget\b", "wget"),
    (r"^git\s+clone\s+https?://", "git-clone-network"),
    (r"^git\s+clone\s+git@", "git-clone-ssh"),

    # Significant git writes.
    (r"^git\s+(push|merge|rebase|tag|cherry-pick|revert)\b", "git-write"),
    (r"^git\s+reset\s+(--hard|--mixed\s+HEAD~\d+|--hard\s+HEAD~\d+)\b", "git-reset-hard"),
    (r"^git\s+clean\s+-f[d]?\b", "git-clean"),

    # Out-of-worktree filesystem mutations. We approximate this by flagging
    # rm/mv/cp targets that look absolute or that hit the parent (`..`).
    (r"^(rm|mv|cp)\s+(-[\w]*\s+)?(/|~)\S+", "fs-mutate-absolute"),
    (r"^(rm|mv|cp)\s+.*\.\./", "fs-mutate-parent"),
    (r"^(del|rmdir)\s+/[sf]?\b", "windows-rmdir-recursive"),

    # Dev servers (long-running, network-binding).
    (r"^npm\s+(run\s+dev|start)\b", "npm-dev-server"),
    (r"^(yarn|pnpm)\s+(dev|start)\b", "yarn-dev-server"),
    (r"^vite\s*(\s+\S*)?$", "vite-dev"),
    (r"^next\s+(dev|start)\b", "next-dev"),
    (r"^python\s+-m\s+http\.server\b", "python-http-server"),
    (r"^uvicorn\b", "uvicorn"),
    (r"^fastapi\s+(run|dev)\b", "fastapi-run"),
    (r"^docker\s+(run|compose\s+up)\b", "docker-run"),

    # Database modifications.
    (r"^(psql|mysql|mongosh|sqlite3)\b", "db-shell"),
    (r"\.(sql)$", "sql-file"),  # heuristic — only flags if cmd ends with .sql
    (r"\b(drop|delete|truncate|alter)\s+(database|table|schema)\b", "sql-destructive"),

    # Environment changes.
    (r"^(export|set|setx)\s+\w+=", "env-set"),
    (r"\s>>?\s*~?/?\.(bashrc|zshrc|profile|env)\b", "shell-rc-write"),
    (r"\s>>?\s*\.env\b", "dotenv-write"),
]


# Pre-compile.
_BLOCKED: list[PolicyRule] = [
    PolicyRule(re.compile(p, re.IGNORECASE), label, rationale)
    for p, label, rationale in ALWAYS_BLOCKED_PATTERNS
]
_ALLOWED: list[PolicyRule] = [
    PolicyRule(re.compile(p, re.IGNORECASE), label, "Safe / read-only operation.")
    for p, label in ALWAYS_ALLOWED_PATTERNS
]
_CONFIRM: list[PolicyRule] = [
    PolicyRule(re.compile(p, re.IGNORECASE), label, "Side-effecting; user OK required.")
    for p, label in REQUIRES_CONFIRMATION_PATTERNS
]


@dataclass(frozen=True)
class Decision:
    classification: CommandClassification
    matched_pattern: str | None
    rule_source: str   # 'system' | 'custom'
    rationale: str


def _normalise(cmd: str) -> str:
    """Collapse runs of whitespace, strip, lowercase. The patterns are
    case-insensitive so the lowercase is belt-and-braces."""
    return re.sub(r"\s+", " ", cmd or "").strip().lower()


def classify_command(cmd: str, custom_rules: list[dict] | None = None) -> Decision:
    """Classify a single shell command.

    `custom_rules` is the user's list from `~/.hive/custom_policies.json`
    (see `approval_mode.py`). Each entry: `{"pattern": str, "action":
    "ALLOW" | "CONFIRM" | "BLOCK"}`. Custom rules layer **before** the
    built-in lists — they let users tighten or loosen specific commands.
    """
    normalised = _normalise(cmd)
    if not normalised:
        return Decision(
            CommandClassification.BLOCKED, None, "system",
            "Empty command.",
        )

    # 1. Custom rules first.
    for rule in custom_rules or []:
        try:
            if re.search(rule["pattern"], normalised, re.IGNORECASE):
                action = rule.get("action", "CONFIRM").upper()
                if action == "ALLOW":
                    return Decision(
                        CommandClassification.ALLOWED, rule["pattern"], "custom",
                        f"User custom rule: ALLOW {rule['pattern']!r}",
                    )
                if action == "BLOCK":
                    return Decision(
                        CommandClassification.BLOCKED, rule["pattern"], "custom",
                        f"User custom rule: BLOCK {rule['pattern']!r}",
                    )
                if action == "CONFIRM":
                    return Decision(
                        CommandClassification.CONFIRMATION, rule["pattern"], "custom",
                        f"User custom rule: CONFIRM {rule['pattern']!r}",
                    )
        except re.error:
            # Malformed user pattern — ignore, fall through to system rules.
            continue

    # 2. System BLOCKED (non-overridable).
    for rule in _BLOCKED:
        if rule.pattern.search(normalised):
            return Decision(
                CommandClassification.BLOCKED, rule.pattern.pattern, "system", rule.rationale,
            )

    # 3. System ALLOWED.
    for rule in _ALLOWED:
        if rule.pattern.search(normalised):
            return Decision(
                CommandClassification.ALLOWED, rule.pattern.pattern, "system", rule.rationale,
            )

    # 4. System CONFIRMATION.
    for rule in _CONFIRM:
        if rule.pattern.search(normalised):
            return Decision(
                CommandClassification.CONFIRMATION, rule.pattern.pattern, "system", rule.rationale,
            )

    # 5. Default — be safe.
    return Decision(
        CommandClassification.CONFIRMATION, None, "system",
        "Unrecognised command — defaulting to confirmation.",
    )
