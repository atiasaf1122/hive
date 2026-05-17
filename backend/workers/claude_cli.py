"""ClaudeCLIWorker — runs `claude` CLI as a subprocess.

Uses OAuth via CLAUDE_CODE_OAUTH_TOKEN env var (Claude Max subscription).
Each run() call spawns a fresh subprocess in the agent's git worktree.
Process groups ensure clean termination of the entire child process tree.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import AsyncIterator
from typing import ClassVar

from backend.workers.base import EventType, HiveEvent, WorkerConfig
from backend.workers.stream_parser import parse_stream

logger = logging.getLogger(__name__)

# Idle timeout: if the stream produces no data for this long, fail fast.
_IDLE_TIMEOUT_S = float(os.environ.get("CLAUDE_STREAM_IDLE_TIMEOUT_MS", "600000")) / 1000


class ClaudeCLIWorker:
    """Worker implementation that delegates to the `claude` CLI subprocess.

    The orchestrator never imports this class directly — it receives a Worker
    Protocol instance. Keeping the implementation here allows swapping without
    touching orchestrator code.
    """

    # Track running processes: agent_id -> (process, pgid)
    _processes: ClassVar[dict[str, tuple[asyncio.subprocess.Process, int]]] = {}

    def __init__(self, oauth_token: str | None = None) -> None:
        # Token can be injected or read from env at run time.
        self._oauth_token = oauth_token or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")

    async def run(self, prompt: str, config: WorkerConfig) -> AsyncIterator[HiveEvent]:
        """Spawn claude CLI and stream events until completion."""
        full_prompt = (config.system_prompt + chr(10) + chr(10) + prompt) if config.system_prompt else prompt
        cmd = [
            "claude",
            "-p", full_prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--dangerously-skip-permissions",
            "--max-turns", str(config.max_turns),
        ]

        env = {**os.environ, **config.env_overrides}
        if self._oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = self._oauth_token

        if config.model and config.model.startswith("claude:"):
            model_name = config.model.split(":", 1)[1]
            # Map shorthand to full model ID
            model_name = _resolve_model(model_name)
            cmd += ["--model", model_name]

        logger.info(
            "Spawning claude CLI | agent=%s session=%s cwd=%s",
            config.agent_id,
            config.session_id,
            config.worktree_path,
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=config.worktree_path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Create a new process group so we can kill the whole tree cleanly.
            start_new_session=True,
        )

        pgid = os.getpgid(proc.pid)
        self._processes[config.agent_id] = (proc, pgid)

        try:
            async for event in parse_stream(proc.stdout, config):  # type: ignore[arg-type]
                yield event

                # If the stream signals a rate limit, wait before next read.
                if event.type == EventType.RATE_LIMIT and event.retry_after_ms:
                    wait_s = event.retry_after_ms / 1000
                    logger.warning(
                        "Rate limit hit for agent=%s — waiting %.1fs", config.agent_id, wait_s
                    )
                    await asyncio.sleep(wait_s)

            await proc.wait()
            exit_code = proc.returncode

            if exit_code != 0:
                stderr = b""
                if proc.stderr:
                    stderr = await proc.stderr.read()
                error_msg = stderr.decode(errors="replace").strip() or f"exit code {exit_code}"
                logger.error("claude CLI exited with error | agent=%s: %s", config.agent_id, error_msg)
                yield HiveEvent(
                    type=EventType.AGENT_ERROR,
                    agent_id=config.agent_id,
                    session_id=config.session_id,
                    error=error_msg,
                )
            else:
                yield HiveEvent(
                    type=EventType.AGENT_END,
                    agent_id=config.agent_id,
                    session_id=config.session_id,
                )
        except asyncio.CancelledError:
            await self.kill(config.agent_id)
            raise
        finally:
            self._processes.pop(config.agent_id, None)

    async def kill(self, agent_id: str) -> None:
        """Terminate the entire process group for an agent."""
        entry = self._processes.get(agent_id)
        if entry is None:
            return
        proc, pgid = entry
        logger.info("Killing process group pgid=%d for agent=%s", pgid, agent_id)
        try:
            os.killpg(pgid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # already gone
        finally:
            self._processes.pop(agent_id, None)


def _resolve_model(shorthand: str) -> str:
    """Map friendly model names to full Claude model IDs."""
    _MAP = {
        "opus": "claude-opus-4-7",
        "opus-4": "claude-opus-4-7",
        "sonnet": "claude-sonnet-4-6",
        "sonnet-4": "claude-sonnet-4-6",
        "haiku": "claude-haiku-4-5",
        "haiku-4": "claude-haiku-4-5",
    }
    return _MAP.get(shorthand.lower(), shorthand)
