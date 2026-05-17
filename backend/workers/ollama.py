"""OllamaWorker — delegates to a local Ollama HTTP server.

Streams tokens from http://localhost:11434/api/generate and normalizes
the output into the same HiveEvent format as ClaudeCLIWorker.

If Ollama is not running, run() immediately yields AGENT_ERROR with a
clear message — no exceptions leak to the orchestrator.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from backend.workers.base import EventType, HiveEvent, WorkerConfig

logger = logging.getLogger(__name__)

_OLLAMA_BASE = "http://localhost:11434"
_CONNECT_TIMEOUT = 5.0   # seconds — fast fail if Ollama not running
_READ_TIMEOUT = 120.0    # seconds per chunk during generation


class OllamaWorker:
    """Worker implementation backed by a local Ollama server.

    Model is specified in WorkerConfig.model as "ollama:<model_name>",
    e.g. "ollama:llama3.1" or "ollama:qwen2.5".
    """

    def __init__(self, base_url: str = _OLLAMA_BASE) -> None:
        self._base_url = base_url

    async def run(self, prompt: str, config: WorkerConfig) -> AsyncIterator[HiveEvent]:
        """Stream generation from Ollama, yielding HiveEvents."""
        model_name = _parse_model(config.model)
        base = dict(agent_id=config.agent_id, session_id=config.session_id)

        full_prompt = prompt
        if config.system_prompt:
            full_prompt = f"{config.system_prompt}\n\n{prompt}"

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
