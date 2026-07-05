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

from backend.models import resolve_cli_model
from backend.workers.base import EventType, HiveEvent, WorkerConfig
from backend.workers.stream_parser import parse_stream

logger = logging.getLogger(__name__)


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
        from backend.detection import resolved_claude_path

        full_prompt = (config.system_prompt + chr(10) + chr(10) + prompt) if config.system_prompt else prompt
        cmd = [
            # Absolute path resolved by detection so we don't depend on
            # the launching shell's PATH including the install dir.
            resolved_claude_path(),
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
            # Tier aliases ('sonnet', 'opus', 'haiku') pass through — the
            # claude CLI resolves them to the latest model itself, so HIVE
            # never pins dated IDs. See backend/models.py.
            cmd += ["--model", resolve_cli_model(model_name)]

        # C2: per-agent MCP servers. --strict-mcp-config is the important
        # half — without it the CLI merges the user's global ~/.claude.json
        # servers into every worker (verified against `claude --help`).
        if config.mcp_config_path:
            cmd += ["--mcp-config", config.mcp_config_path, "--strict-mcp-config"]

        # B2: conversation continuity. First spawn names the conversation
        # (--session-id); re-spawns of the same logical agent resume it
        # (--resume) so context carries across turns / capability re-spawns.
        if config.claude_session_id:
            if config.resume_claude_session:
                cmd += ["--resume", config.claude_session_id]
            else:
                cmd += ["--session-id", config.claude_session_id]

        # Allowed-tools whitelist — used by the planner to enforce
        # read-only access (it kept silently writing files in /tmp
        # instead of just deciding the team composition). When set
        # explicitly to a non-empty list, only those tools are permitted.
        #
        # We explicitly REFUSE an empty list rather than passing
        # `--allowed-tools ""` to the claude CLI. The CLI's behaviour on
        # an empty CSV is undocumented (could be "block all" or "ignore
        # the flag") — both modes would be silent footguns for callers
        # that meant one but got the other. An empty list almost always
        # means a caller bug, so raise.
        if config.allowed_tools is not None:
            if not config.allowed_tools:
                raise ValueError(
                    "WorkerConfig.allowed_tools=[] is rejected — pass None "
                    "to disable the flag, or pass at least one tool name."
                )
            cmd += ["--allowed-tools", ",".join(config.allowed_tools)]

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

        stalled = False
        try:
            async for event in parse_stream(proc.stdout, config):  # type: ignore[arg-type]
                # Stamp the subprocess PID onto the start event so the
                # orchestrator can persist it (agents.pid) — recovery uses
                # it to tell a live agent from a crashed one after restart.
                if event.type == EventType.AGENT_START and event.pid is None:
                    event.pid = proc.pid
                if event.type == EventType.AGENT_ERROR and "idle-timeout" in (event.error or ""):
                    stalled = True
                yield event

                # If the stream signals a rate limit, wait before next read.
                if event.type == EventType.RATE_LIMIT and event.retry_after_ms:
                    wait_s = event.retry_after_ms / 1000
                    logger.warning(
                        "Rate limit hit for agent=%s — waiting %.1fs", config.agent_id, wait_s
                    )
                    await asyncio.sleep(wait_s)

            if stalled:
                # The process is hung, not finished — `await proc.wait()`
                # would block forever. Kill the whole process group; the
                # parser already yielded the AGENT_ERROR, so no AGENT_END.
                logger.error(
                    "claude CLI stalled (idle-timeout) | agent=%s — killing process group",
                    config.agent_id,
                )
                await self.kill(config.agent_id)
                return

            await proc.wait()
            exit_code = proc.returncode

            if exit_code != 0:
                stderr = b""
                if proc.stderr:
                    stderr = await proc.stderr.read()
                # Always include the exit code even when stderr is empty —
                # an "exit code 1" with no stderr is exactly the failure
                # mode we hit dogfooding (likely rate-limit / token-expiry).
                # Surfacing the exit code separately lets the UI distinguish
                # "claude exited cleanly with an error message" from "claude
                # crashed without saying anything".
                stderr_text = stderr.decode(errors="replace").strip()
                error_msg = (
                    f"claude exited {exit_code}"
                    + (f": {stderr_text}" if stderr_text else " (no stderr captured)")
                )
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
