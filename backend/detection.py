"""Backend availability detection — runs at HIVE startup.

Checks which Worker backends are available on the current machine and
returns a BackendStatus. The orchestrator uses this to decide which
Worker implementation to instantiate for a given agent role.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# Cache of the FIRST URL that actually answered an Ollama probe. Workers
# read this so they don't re-discover on every spawn. Cleared whenever
# detect_backends() runs (so a restart picks up the user changing
# Settings → ollamaEndpoint).
#
# Guarded by `_ollama_base_lock` because detect_backends() can be re-run
# (e.g. on a Settings change) while an OllamaWorker is reading the cache
# on another task — without the lock a worker could capture a transient
# None and stick with the default URL.
_RESOLVED_OLLAMA_BASE: str | None = None
_ollama_base_lock = threading.Lock()

# Same pattern for the resolved claude CLI path — see _locate_claude_binary
# for the search strategy. ClaudeCLIWorker reads this so we don't need to
# rely on the launching shell's PATH including the install dir.
_RESOLVED_CLAUDE_PATH: str | None = None
_claude_path_lock = threading.Lock()

# Common install locations claude ends up in, ordered by how likely the
# user picked them. Probed in order when shutil.which("claude") fails.
_CLAUDE_PROBE_PATHS: tuple[str, ...] = (
    "~/.local/bin/claude",                         # default installer location
    "~/.npm-global/bin/claude",                    # npm prefix override
    "~/.bun/bin/claude",                           # bun install
    "/usr/local/bin/claude",
    "/opt/homebrew/bin/claude",                    # macOS arm64 brew
    "/home/linuxbrew/.linuxbrew/bin/claude",       # linux brew
)


def resolved_ollama_base() -> str:
    """Return the URL the worker should hit. Falls back to the configured
    base when nothing has been discovered yet (so dev-on-Linux still works)."""
    with _ollama_base_lock:
        return _RESOLVED_OLLAMA_BASE or _OLLAMA_BASE


def _set_resolved_ollama_base(value: str | None) -> None:
    global _RESOLVED_OLLAMA_BASE
    with _ollama_base_lock:
        _RESOLVED_OLLAMA_BASE = value


def resolved_claude_path() -> str:
    """Return the absolute path to the claude binary detection found.

    Workers use this instead of bare "claude" so spawning succeeds even
    when the launching shell's PATH doesn't include the install dir
    (the default WSL login PATH doesn't include ~/.local/bin, which is
    where the official installer drops the symlink). Falls back to
    "claude" so a still-working PATH-based setup keeps working.
    """
    with _claude_path_lock:
        return _RESOLVED_CLAUDE_PATH or "claude"


def _set_resolved_claude_path(value: str | None) -> None:
    global _RESOLVED_CLAUDE_PATH
    with _claude_path_lock:
        _RESOLVED_CLAUDE_PATH = value


def _locate_claude_binary() -> str | None:
    """Find an executable claude binary anywhere it's likely to be.

    Order: shutil.which (honours $PATH) first, then a fixed list of
    common install dirs that the user's login PATH might not include.
    Returns an absolute path or None.
    """
    found = shutil.which("claude")
    if found:
        return found
    for raw in _CLAUDE_PROBE_PATHS:
        candidate = Path(os.path.expanduser(raw))
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
            # Some installers drop a symlink to a versioned directory or
            # a launcher script; treat symlinks as candidates too.
            if candidate.is_symlink():
                return str(candidate)
        except OSError:
            continue
    return None


@dataclass
class BackendStatus:
    claude_cli: bool = False
    claude_cli_version: str = ""
    claude_api: bool = False
    ollama: bool = False
    ollama_models: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.claude_cli:
            parts.append(f"ClaudeCLI({self.claude_cli_version})")
        if self.claude_api:
            parts.append("ClaudeAPI")
        if self.ollama:
            models = ", ".join(self.ollama_models) or "no models pulled"
            parts.append(f"Ollama[{models}]")
        return " | ".join(parts) if parts else "no backends available"


async def detect_backends() -> BackendStatus:
    """Probe all backends concurrently. Safe to call at startup."""
    status = BackendStatus()

    cli_task = asyncio.create_task(_check_claude_cli())
    api_task = asyncio.create_task(_check_claude_api())
    ollama_task = asyncio.create_task(_check_ollama())

    cli_result, api_result, ollama_result = await asyncio.gather(
        cli_task, api_task, ollama_task, return_exceptions=True
    )

    if isinstance(cli_result, tuple):
        status.claude_cli, status.claude_cli_version = cli_result
    if isinstance(api_result, bool):
        status.claude_api = api_result
    if isinstance(ollama_result, tuple):
        status.ollama, status.ollama_models = ollama_result

    logger.info("Backend detection: %s", status.summary())
    return status


async def _check_claude_cli() -> tuple[bool, str]:
    path = _locate_claude_binary()
    if not path:
        _set_resolved_claude_path(None)
        return False, ""
    try:
        proc = await asyncio.create_subprocess_exec(
            path, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        version = stdout.decode().strip().split("\n")[0]
    except Exception as exc:
        logger.debug("claude CLI check failed at %s: %s", path, exc)
        _set_resolved_claude_path(None)
        return False, ""

    # Cache the path workers will use so they don't all re-do the probe
    # (and so they don't depend on PATH being set the same way the
    # backend process had it at start-up).
    _set_resolved_claude_path(path)
    return True, version


async def _check_claude_api() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", ""))


def _windows_host_ip() -> str | None:
    """Return the Windows host IP reachable from WSL2, or None if not on WSL.

    WSL2 is a separate VM — `localhost` from inside WSL doesn't reach
    Windows-side services. To find the host we use the IPv4 default
    route's gateway (always the Windows host on a stock WSL setup).

    We deliberately do NOT read /etc/resolv.conf's `nameserver` line:
    on machines with Tailscale, Cloudflare WARP, or any DNS-rewriting
    VPN, that points at the tunnel resolver (e.g. 100.100.100.100),
    not the Windows host — caused a real "Ollama not found" miss
    during dogfooding. `ip route show default` reads from the kernel
    routing table and is immune to userspace DNS shenanigans.
    """
    try:
        text = Path("/proc/version").read_text(errors="ignore")
    except OSError:
        return None
    if not any(tag in text for tag in ("Microsoft", "microsoft", "WSL")):
        return None

    import subprocess
    try:
        out = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines():
        # "default via 172.18.64.1 dev eth0 proto kernel"
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "default" and parts[1] == "via":
            return parts[2]
    return None


async def _check_ollama() -> tuple[bool, list[str]]:
    """Probe Ollama. Tries:
      1. The configured base (OLLAMA_BASE_URL env var, default localhost).
      2. A 127.0.0.1 fallback when the base used 'localhost' — IPv6 first
         resolution can miss an IPv4-only Ollama on some WSL2 setups.
      3. On WSL, the Windows host IP — Ollama running on the Windows side
         is invisible to localhost-in-WSL. Users tell us all the time
         their Ollama is "running" but HIVE can't see it; this catches
         the most common case automatically. The user can still override
         via Settings → ollamaEndpoint for remote / custom hosts.
    """
    candidates = [_OLLAMA_BASE]
    if "localhost" in _OLLAMA_BASE:
        candidates.append(_OLLAMA_BASE.replace("localhost", "127.0.0.1"))
    win_host = _windows_host_ip()
    if win_host:
        candidates.append(f"http://{win_host}:11434")

    last_exc: Exception | None = None
    for base in candidates:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{base}/api/tags")
                if response.status_code != 200:
                    last_exc = RuntimeError(f"HTTP {response.status_code} from {base}")
                    continue
                data = response.json()
                models = [m["name"] for m in data.get("models", [])]
                logger.info("Ollama reachable at %s — %d models", base, len(models))
                _set_resolved_ollama_base(base)
                return True, models
        except Exception as exc:
            last_exc = exc
            logger.debug("Ollama check failed at %s: %s", base, exc)
    if last_exc:
        logger.debug("Ollama unreachable on all candidates: %s", last_exc)
    _set_resolved_ollama_base(None)
    return False, []
