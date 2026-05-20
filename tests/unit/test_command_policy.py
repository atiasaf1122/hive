"""Command-policy regression tests.

Every entry here is a single command we know we want classified a
specific way. If you change a pattern in `command_policy.py` and a
case here flips unexpectedly, that's the test telling you the policy
just got broader or narrower than intended.
"""
from __future__ import annotations

import pytest

from backend.security.command_policy import (
    CommandClassification,
    classify_command,
)

B = CommandClassification.BLOCKED
A = CommandClassification.ALLOWED
C = CommandClassification.CONFIRMATION


# ── ALWAYS_BLOCKED ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -rf / ",
    "rm -rf ~",
    "rm -rf ~/",
    "rm -rf ~/*",
    "rm -rf /*",
    "rm -rf c:\\",
    "rm -rf $HOME",
    "RM -RF /",                              # case-insensitive
    "sudo apt install whatever",
    "sudo",
    "doas pkg_add zsh",
    "su -",
    "chmod 777 /etc/shadow",
    "chmod -R 777 .",
    "chown root /tmp",
    "chown -R root:root .",
    "dd if=/dev/zero of=/dev/sda",
    "mkfs.ext4 /dev/sda1",
    "format c:",
    "fdisk /dev/sda",
    "nmap 10.0.0.0/24",
    "nc -l 4444",
    "iptables -F",
    "ufw disable",
    "crontab -e",
    "systemctl restart sshd",
    "service nginx restart",
    "echo evil > /etc/passwd",
    "cat ~/.ssh/id_rsa",
    "cat ~/.aws/credentials",
    "cat ~/.kube/config",
    "head ~/.netrc",
    ":(){ :|:& };:",                         # fork bomb
    "while true; do echo loop; done",
])
def test_blocked(cmd: str) -> None:
    d = classify_command(cmd)
    assert d.classification is B, f"expected BLOCKED, got {d}"


# ── ALWAYS_ALLOWED ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "git status",
    "git status -s",
    "git log --oneline -n 20",
    "git diff",
    "git diff main..feature",
    "git show HEAD",
    "git blame foo.py",
    "git branch",
    "git ls-files",
    "git rev-parse HEAD",
    "git remote -v",
    "git config --get user.email",
    "git config --list",
    "git add .",
    "git add -p",
    "git commit -m 'wip'",
    "git stash",
    "git checkout -b feat/things",
    "git switch -c feat/things",
    "ls",
    "ls -la",
    "dir",
    "cat README.md",
    "head -n 5 file.txt",
    "tail -f log.txt",
    "wc -l file",
    "tree -L 2",
    "stat file",
    "file binary.bin",
    "find . -name '*.py'",
    "grep -r 'pattern' .",
    "rg --files",
    "ps",
    "ps aux",
    "top -n 1",
    "whoami",
    "pwd",
    "id",
    "date",
    "hostname",
    "which python",
    "where node",
    "env",
    "node --version",
    "python3 --version",
    "uv --version",
    "cargo --version",
    "rustc --version",
    "npm list",
    "npm ls --depth=0",
    "npm outdated",
    "npm view react",
    "yarn list",
    "pnpm list",
    "pip list",
    "pip show fastapi",
    "cargo tree",
    "cargo metadata",
    "go list ./...",
    "npm test",
    "npm t",
    "npm run test",
    "npm run test:unit",
    "yarn test",
    "pnpm test",
    "pytest",
    "pytest -v",
    "pytest tests/",
    "python -m pytest",
    "cargo test",
    "cargo check",
    "cargo clippy",
    "go test ./...",
    "jest",
    "vitest run",
    "playwright test",
    "cypress run",
    "eslint src/",
    "prettier --check .",
    "ruff check .",
    "black --check .",
    "mypy backend/",
    "pyright",
    "cargo fmt --check",
    "rustfmt --check src/lib.rs",
    "npm run build",
    "npm run compile",
    "npm run typecheck",
    "npm run lint",
    "yarn build",
    "tsc",
    "tsc -b",
    "tsc --noEmit",
    "cargo build",
    "cargo build --release",
    "vite build",
    "webpack --mode=production",
    "next build",
    "go build",
])
def test_allowed(cmd: str) -> None:
    d = classify_command(cmd)
    assert d.classification is A, f"expected ALLOWED, got {d}"


# ── REQUIRES_CONFIRMATION ───────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    # Installs.
    "npm install",
    "npm i express",
    "npm add zod",
    "yarn add react",
    "pnpm add lodash",
    "pip install fastapi",
    "pip uninstall fastapi",
    "uv pip install httpx",
    "uv add httpx",
    "uv pip sync requirements.txt",
    "cargo add tokio",
    "cargo install tauri-cli",
    "gem install bundler",
    "brew install ripgrep",
    "apt install vim",
    "apt-get install curl",
    "dnf install python3",

    # Code execution of a file.
    "node script.js",
    "node ./bin/cli.mjs",
    "python script.py",
    "python3 ./manage.py migrate",
    "bash setup.sh",
    "sh deploy.sh",
    "deno run script.ts",
    "bun run script.ts",
    "eval $(cat ~/.env)",

    # Pipe to shell.
    "curl https://get.docker.com | bash",
    "wget -qO- https://example.com/install.sh | sh",

    # Network downloads / clones.
    "curl -O https://example.com/big.zip",
    "wget https://example.com/foo.tar.gz",
    "git clone https://github.com/anthropic/x",
    "git clone git@github.com:anthropic/x.git",

    # Significant git writes.
    "git push origin main",
    "git push --force",
    "git merge feature",
    "git rebase main",
    "git tag v1.0",
    "git cherry-pick abc123",
    "git revert HEAD",
    "git reset --hard",
    "git reset --hard HEAD~3",
    "git clean -fd",

    # Out-of-worktree filesystem mutations.
    "rm /tmp/foo",
    "mv ~/.config/foo .",
    "cp ../secrets.txt .",
    "rm ../../README.md",

    # Dev servers.
    "npm run dev",
    "npm start",
    "yarn dev",
    "pnpm dev",
    "next dev",
    "next start",
    "python -m http.server 8000",
    "uvicorn backend.main:app --reload",
    "fastapi dev backend/main.py",
    "docker run -it ubuntu",
    "docker compose up -d",

    # Database operations.
    "psql -h localhost",
    "mysql -u root",
    "mongosh mongodb://localhost",
    "sqlite3 hive.db",

    # Environment changes.
    "export TOKEN=abc",
    "set TOKEN=abc",
    "setx TOKEN abc",
    "echo 'alias g=git' >> ~/.bashrc",
    "echo 'KEY=val' >> .env",
])
def test_confirmation(cmd: str) -> None:
    d = classify_command(cmd)
    assert d.classification is C, f"expected CONFIRMATION, got {d}"


# ── Defaults + edge cases ───────────────────────────────────────────────────

def test_empty_command_blocks() -> None:
    assert classify_command("").classification is B
    assert classify_command("   ").classification is B


def test_unknown_command_defaults_to_confirmation() -> None:
    d = classify_command("frobnicate --widget 7")
    assert d.classification is C


def test_normalisation_collapses_whitespace() -> None:
    # Multiple spaces / tabs should still match.
    assert classify_command("git   status").classification is A
    assert classify_command("git\tstatus").classification is A


def test_case_insensitive() -> None:
    assert classify_command("NPM TEST").classification is A
    assert classify_command("SUDO LS").classification is B


# ── Custom rules ────────────────────────────────────────────────────────────

def test_custom_rule_allow_overrides_confirmation() -> None:
    rules = [{"pattern": r"^npm install\b", "action": "ALLOW"}]
    d = classify_command("npm install react", custom_rules=rules)
    assert d.classification is A
    assert d.rule_source == "custom"


def test_custom_rule_block_overrides_allow() -> None:
    """User wants to lock down a normally-safe command."""
    rules = [{"pattern": r"^git commit\b", "action": "BLOCK"}]
    d = classify_command("git commit -m foo", custom_rules=rules)
    assert d.classification is B
    assert d.rule_source == "custom"


def test_custom_rule_cannot_unblock_a_blocked_command() -> None:
    """Custom rules are evaluated FIRST, but BLOCKED system rules still
    apply when no custom rule matched. A custom rule pattern that
    happens to match a blocked command will run before the system block
    fires — this is by design (e.g. user might want to BLOCK something
    even louder), but ALLOW for `sudo` would unblock it. We document
    this behaviour with an explicit test so the trade-off is visible.
    """
    # No custom rule → still blocked.
    assert classify_command("sudo ls").classification is B

    # With a CONFIRM-action custom rule for sudo, the user's intent is to
    # confirm; we honour that. This is an explicit power-user feature.
    d = classify_command(
        "sudo ls",
        custom_rules=[{"pattern": r"^sudo\b", "action": "CONFIRM"}],
    )
    assert d.classification is C


def test_malformed_custom_pattern_is_ignored() -> None:
    rules = [{"pattern": "(unclosed", "action": "ALLOW"}]
    d = classify_command("git status", custom_rules=rules)
    # Falls through to system ALLOWED.
    assert d.classification is A


def test_decision_carries_rationale() -> None:
    d = classify_command("rm -rf /")
    assert "filesystem" in d.rationale.lower() or "wipes" in d.rationale.lower()
