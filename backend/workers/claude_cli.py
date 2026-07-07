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
import time
from collections import deque
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

        # D0.1: drain stderr CONCURRENTLY into a bounded buffer. Reading it
        # only after exit risks a pipe-full deadlock, and a killed process
        # loses it entirely; failures must be diagnosable from the event log.
        stderr_buf = bytearray()

        async def _drain_stderr() -> None:
            try:
                while True:
                    chunk = await proc.stderr.read(1024)  # type: ignore[union-attr]
                    if not chunk:
                        return
                    stderr_buf.extend(chunk)
                    del stderr_buf[:-2048]  # keep only the last ~2KB
            except Exception:  # noqa: BLE001
                return

        stderr_task = asyncio.create_task(_drain_stderr())
        raw_tail: deque[str] = deque(maxlen=3)

        stalled = False
        started = time.monotonic()
        yielded = 0
        try:
            async for event in parse_stream(proc.stdout, config, raw_tail=raw_tail):  # type: ignore[arg-type]
                yielded += 1
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
            try:
                await asyncio.wait_for(stderr_task, timeout=2.0)
            except asyncio.TimeoutError:
                stderr_task.cancel()
            exit_code = proc.returncode

            runtime_s = time.monotonic() - started
            if exit_code != 0:
                # D0.1: the C5 dogfooding failure mode was 'claude exited 1
                # (no stderr captured)' repeated with zero diagnosis. Include
                # the stderr tail; when stderr is genuinely empty, include
                # the last stdout NDJSON lines instead so the event log
                # always says what the process last did.
                stderr_text = bytes(stderr_buf).decode(errors="replace").strip()
                if stderr_text:
                    detail = f": stderr tail: {stderr_text[-2048:]}"
                elif raw_tail:
                    detail = " (empty stderr; last stdout lines: " + " | ".join(raw_tail) + ")"
                else:
                    detail = " (empty stderr, no stdout captured)"
                error_msg = (f"claude exited {exit_code} after {runtime_s:.1f}s"
                             f"{detail}")
                logger.error("claude CLI exited with error | agent=%s: %s", config.agent_id, error_msg)
                yield HiveEvent(
                    type=EventType.AGENT_ERROR,
                    agent_id=config.agent_id,
                    session_id=config.session_id,
                    error=error_msg,
                    # Post-1.0 close-out: a process that died having emitted
                    # ZERO events never let the agent act — that's the
                    # harness/CLI, not the agent. With events, exit codes
                    # alone still can't assign fault (D0.2).
                    origin="infrastructure" if yielded == 0 else "unknown",
                )
            elif yielded == 0:
                # Exit 0 with NO parsed events used to masquerade as a clean
                # AGENT_END — an empty silent death. Surface it instead.
                stderr_text = bytes(stderr_buf).decode(errors="replace").strip()
                detail = (f"; stderr tail: {stderr_text[-2048:]}" if stderr_text
                          else "; empty stderr, no parseable stdout")
                error_msg = (f"claude exited 0 without emitting any events "
                             f"after {runtime_s:.1f}s{detail}")
                logger.error("%s | agent=%s", error_msg, config.agent_id)
                yield HiveEvent(
                    type=EventType.AGENT_ERROR,
                    agent_id=config.agent_id,
                    session_id=config.session_id,
                    error=error_msg,
                    origin="infrastructure",
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
            if not stderr_task.done():
                stderr_task.cancel()
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
