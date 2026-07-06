"""OllamaWorker — delegates to a local Ollama HTTP server.

Streams tokens from http://localhost:11434/api/generate and normalizes
the output into the same HiveEvent format as ClaudeCLIWorker.

If Ollama is not running, run() immediately yields AGENT_ERROR with a
clear message — no exceptions leak to the orchestrator.

E2: local models have no tool loop, so file changes ride a strict output
format instead — the worker instructs the model to emit full file
contents in <<<FILE: path>>> ... <<<END FILE>>> blocks, then writes each
block into the agent's worktree (traversal-guarded) and reports it as a
TOOL_USE event. HIVE's auto-commit → validation → merge machinery then
treats a local worker exactly like a Claude one. Deliberately mechanical:
that is the class of subtask the planner routes to local models.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from backend.workers.base import EventType, HiveEvent, WorkerConfig

logger = logging.getLogger(__name__)

_FILE_BLOCK_RE = re.compile(
    r"<<<FILE:\s*(?P<path>[^>]+?)\s*>>>\r?\n?(?P<content>.*?)<<<END FILE>>>",
    re.DOTALL,
)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

_FILE_INSTRUCTIONS = """
## How to change files (you have NO shell and NO tools)
To create or fully overwrite a file, emit a block in EXACTLY this form:

<<<FILE: relative/path/from/repo/root>>>
<entire new file content>
<<<END FILE>>>

Rules: one block per file; always the COMPLETE file content (no diffs,
no ellipses); paths stay inside the repository. Text outside blocks is
your report — state what you changed and why, briefly. If you cannot do
the task, write no blocks and explain why in the report.
"""


def _apply_file_blocks(text: str, worktree: Path) -> tuple[list[str], list[str]]:
    """Write every well-formed file block under the worktree.

    Returns (written paths, refused paths). Refusals: absolute paths or
    anything escaping the worktree after resolution.
    """
    written: list[str] = []
    refused: list[str] = []
    root = worktree.resolve()
    for match in _FILE_BLOCK_RE.finditer(text):
        rel = match.group("path").strip()
        content = match.group("content")
        candidate = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
        if not candidate.is_relative_to(root):
            refused.append(rel)
            continue
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(content)
        written.append(rel)
    return written, refused

_OLLAMA_BASE = "http://localhost:11434"
_CONNECT_TIMEOUT = 5.0   # seconds — fast fail if Ollama not running
_READ_TIMEOUT = 120.0    # seconds per chunk during generation


class OllamaWorker:
    """Worker implementation backed by a local Ollama server.

    Model is specified in WorkerConfig.model as "ollama:<model_name>",
    e.g. "ollama:llama3.1" or "ollama:qwen2.5".

    When `base_url` is left as the default we ask `backend.detection`
    for the URL it last verified working. On WSL2 that's typically
    the Windows host (`http://172.18.x.x:11434`) rather than
    `localhost:11434` — the latter doesn't traverse the WSL VM.
    """

    def __init__(self, base_url: str | None = None) -> None:
        if base_url is None:
            from backend.detection import resolved_ollama_base
            base_url = resolved_ollama_base()
        self._base_url = base_url

    async def run(self, prompt: str, config: WorkerConfig) -> AsyncIterator[HiveEvent]:
        """Stream generation from Ollama, yielding HiveEvents."""
        model_name = _parse_model(config.model)
        base = dict(agent_id=config.agent_id, session_id=config.session_id)

        full_prompt = prompt
        if config.system_prompt:
            full_prompt = f"{config.system_prompt}\n\n{prompt}"
        worktree = Path(config.worktree_path) if config.worktree_path else None
        if worktree is not None:
            full_prompt = f"{full_prompt}\n{_FILE_INSTRUCTIONS}"

        payload = {
            "model": model_name,
            "prompt": full_prompt,
            "stream": True,
        }

        yield HiveEvent(type=EventType.AGENT_START, **base)

        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT, write=10.0, pool=10.0),
            ) as client:
                async with client.stream("POST", "/api/generate", json=payload) as response:
                    if response.status_code != 200:
                        error_body = await response.aread()
                        error_msg = error_body.decode(errors="replace")
                        logger.error(
                            "Ollama returned HTTP %d for agent=%s: %s",
                            response.status_code,
                            config.agent_id,
                            error_msg,
                        )
                        yield HiveEvent(
                            type=EventType.AGENT_ERROR,
                            error=f"Ollama HTTP {response.status_code}: {error_msg[:200]}",
                            **base,
                        )
                        return

                    total_tokens_prompt = 0
                    total_tokens_completion = 0
                    collected: list[str] = []

                    async for raw_line in response.aiter_lines():
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        try:
                            chunk = json.loads(raw_line)
                        except json.JSONDecodeError:
                            logger.debug("Non-JSON chunk from Ollama: %s", raw_line[:80])
                            continue

                        token_text = chunk.get("response", "")
                        if token_text:
                            collected.append(token_text)
                            yield HiveEvent(
                                type=EventType.TEXT_DELTA,
                                text=token_text,
                                **base,
                            )

                        # Final chunk contains usage stats
                        if chunk.get("done"):
                            total_tokens_prompt = chunk.get("prompt_eval_count", 0)
                            total_tokens_completion = chunk.get("eval_count", 0)
                            break

                    # Strip thinking spans (qwen3-style models) before any
                    # parsing — <think> content is neither report nor file.
                    full_text = _THINK_RE.sub("", "".join(collected)).strip()

                    if worktree is not None and full_text:
                        written, refused = _apply_file_blocks(full_text, worktree)
                        for rel in written:
                            yield HiveEvent(
                                type=EventType.TOOL_USE,
                                tool_name="write_file",
                                tool_input={"path": rel},
                                **base,
                            )
                        for rel in refused:
                            yield HiveEvent(
                                type=EventType.AGENT_ERROR,
                                error=f"refused file block outside worktree: {rel}",
                                origin="agent",
                                **base,
                            )
                        # The narrative (minus file bodies) is the report the
                        # summarizer and validators read.
                        narrative = _FILE_BLOCK_RE.sub(
                            lambda m: f"[wrote {m.group('path').strip()}]", full_text
                        ).strip()
                        if narrative:
                            yield HiveEvent(type=EventType.TEXT_DONE, text=narrative, **base)
                    elif full_text:
                        yield HiveEvent(type=EventType.TEXT_DONE, text=full_text, **base)

                    yield HiveEvent(
                        type=EventType.COST,
                        input_tokens=total_tokens_prompt,
                        output_tokens=total_tokens_completion,
                        cost_usd=0.0,  # local model — no cost
                        **base,
                    )

        except httpx.ConnectError:
            msg = (
                f"Ollama is not reachable at {self._base_url}. "
                "Make sure Ollama is running (`ollama serve`)."
            )
            logger.warning("agent=%s: %s", config.agent_id, msg)
            yield HiveEvent(type=EventType.AGENT_ERROR, error=msg, **base)
            return
        except httpx.TimeoutException as exc:
            msg = f"Ollama request timed out: {exc}"
            logger.error("agent=%s: %s", config.agent_id, msg)
            yield HiveEvent(type=EventType.AGENT_ERROR, error=msg, **base)
            return

        yield HiveEvent(type=EventType.AGENT_END, **base)

    async def kill(self, agent_id: str) -> None:
        # HTTP streaming — closing the client (done automatically via async with)
        # is sufficient. No persistent process to kill.
        logger.debug("OllamaWorker.kill called for agent=%s (no-op)", agent_id)


def _parse_model(model_spec: str) -> str:
    """Extract model name from 'ollama:llama3.1' -> 'llama3.1'."""
    if ":" in model_spec:
        _, name = model_spec.split(":", 1)
        return name
    return model_spec
