"""Worker interface and unified event format for all backends."""
from __future__ import annotations

import time
from enum import StrEnum
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class EventType(StrEnum):
    # Lifecycle
    AGENT_START = "agent/start"
    AGENT_END = "agent/end"
    AGENT_ERROR = "agent/error"
    MCP_ATTACHED = "mcp/attached"  # orchestrator-emitted: servers equipped at spawn

    # Content
    TEXT_DELTA = "text/delta"
    TEXT_DONE = "text/done"

    # Tool use
    TOOL_USE = "tool/use"
    TOOL_RESULT = "tool/result"

    # System
    RATE_LIMIT = "system/rate_limit"
    COST = "system/cost"

    # Raw passthrough (for debugging)
    RAW = "raw"


class HiveEvent(BaseModel):
    """Unified event emitted by all Worker backends.

    Every backend (Claude CLI, Claude API, Ollama) normalizes its output
    into this format so the orchestrator never needs to know which backend
    produced a given event.
    """

    type: EventType
    agent_id: str
    session_id: str
    ts: float = Field(default_factory=time.time)

    # EventType.TEXT_DELTA / TEXT_DONE
    text: str | None = None

    # EventType.TOOL_USE
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_use_id: str | None = None

    # EventType.TOOL_RESULT
    tool_result: Any | None = None
    tool_result_error: bool = False

    # EventType.COST
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None

    # EventType.AGENT_ERROR / RATE_LIMIT
    error: str | None = None
    retry_after_ms: int | None = None

    # EventType.AGENT_START — OS process id of the spawned worker, when the
    # backend runs one (Claude CLI subprocess). Persisted to agents.pid so
    # startup recovery can distinguish "still running" from "crashed".
    pid: int | None = None

    # EventType.RAW — full original payload for debugging
    raw_payload: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}


class WorkerConfig(BaseModel):
    """Runtime config passed to a Worker when spawning an agent."""

    agent_id: str
    session_id: str
    model: str  # e.g. "claude:sonnet", "ollama:llama3.1"
    worktree_path: str
    system_prompt: str = ""
    max_turns: int = 20
    env_overrides: dict[str, str] = Field(default_factory=dict)
    # Optional whitelist of tool names the agent may use (e.g. for the
    # planner: ["Read", "Grep", "Glob", "WebFetch"]). When None the
    # default claude CLI tool set applies. Empty list disables tools
    # entirely. Threaded into `--allowed-tools` on ClaudeCLIWorker.
    allowed_tools: list[str] | None = None
    # B2: claude CLI conversation identity. When set, the first spawn passes
    # `--session-id <uuid>`; re-spawns of the same logical agent pass
    # `--resume <uuid>` so the agent keeps its conversation context across
    # turns instead of re-exploring the project. Ignored by OllamaWorker.
    claude_session_id: str | None = None
    resume_claude_session: bool = False
    # C2: path to a per-agent MCP config JSON ({"mcpServers": {...}}). When
    # set, ClaudeCLIWorker passes --mcp-config <path> --strict-mcp-config so
    # the agent sees EXACTLY these servers — the user's global ~/.claude.json
    # servers never leak into workers. Ignored by OllamaWorker.
    mcp_config_path: str | None = None


@runtime_checkable
class Worker(Protocol):
    """Protocol that all Worker implementations must satisfy.

    Implementations: ClaudeCLIWorker, ClaudeAPIWorker, OllamaWorker.
    The orchestrator works only with this interface — never with concrete types.
    """

    async def run(
        self, prompt: str, config: WorkerConfig
    ) -> AsyncIterator[HiveEvent]: ...

    async def kill(self, agent_id: str) -> None: ...
