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
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

_OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


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
    if not shutil.which("claude"):
        return False, ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        version = stdout.decode().strip().split("\n")[0]
        return True, version
    except Exception as exc:
        logger.debug("claude CLI check failed: %s", exc)
        return False, ""


async def _check_claude_api() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", ""))


async def _check_ollama() -> tuple[bool, list[str]]:
    """Probe Ollama. Tries the configured base first, then a 127.0.0.1
    fallback (host-as-localhost can resolve to ::1 first and miss an
    IPv4-only Ollama, e.g. on some WSL2 setups)."""
    candidates = [_OLLAMA_BASE]
    if "localhost" in _OLLAMA_BASE:
        candidates.append(_OLLAMA_BASE.replace("localhost", "127.0.0.1"))

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
                return True, models
        except Exception as exc:
            last_exc = exc
            logger.debug("Ollama check failed at %s: %s", base, exc)
    if last_exc:
        logger.debug("Ollama unreachable on all candidates: %s", last_exc)
    return False, []
