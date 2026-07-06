"""Local (Ollama) one-shot caller + internal-task caller selection (E4).

`LocalCaller` mirrors `HaikuCaller`'s callable shape (prompt → str) so it
slots into every existing `haiku_caller=` injection point. Token counts
are logged to cost_log at $0 (they're still context data — E5).

`internal_task_caller` is the selection policy for HIVE's own meta-tasks
(distillation, summarization, classification):

    knob (env HIVE_LOCAL_INTERNAL): "auto" (default) | "on" | "off"
      auto/on → local model when one with the capability is available,
                wrapped with a Haiku fallback on ANY local failure
      off    → Haiku only

A hung local model must never stall session close: LocalCaller enforces
a hard asyncio timeout, and the fallback wrapper turns every local
failure (timeout, HTTP error, Ollama down) into a Haiku call.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S = float(os.environ.get("HIVE_LOCAL_CALL_TIMEOUT_S", "90"))
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass
class LocalCaller:
    """One-shot local generation, HaikuCaller-shaped."""

    model: str                    # bare ollama tag, e.g. "qwen3:8b"
    session_id: str
    agent_id_prefix: str = "local"
    timeout_s: float = _TIMEOUT_S
    _call_counter: int = 0

    async def __call__(self, prompt: str) -> str:
        return await self.invoke(prompt)

    async def invoke(self, prompt: str) -> str:
        from backend.detection import resolved_ollama_base

        self._call_counter += 1
        agent_id = f"{self.agent_id_prefix}-{self.model}-{self.session_id}-{self._call_counter}"

        async def _generate() -> tuple[str, int, int]:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.post(
                    f"{resolved_ollama_base()}/api/generate",
                    json={"model": self.model, "prompt": prompt, "stream": False},
                )
                resp.raise_for_status()
                data = resp.json()
                return (str(data.get("response") or ""),
                        int(data.get("prompt_eval_count") or 0),
                        int(data.get("eval_count") or 0))

        text, tokens_in, tokens_out = await asyncio.wait_for(
            _generate(), timeout=self.timeout_s)

        try:
            from backend.persistence.events import write_cost
            await write_cost(self.session_id, agent_id, tokens_in, tokens_out, 0.0)
        except Exception as exc:  # noqa: BLE001 — cost logging is best-effort
            logger.debug("Local cost write failed: %s", exc)

        return _THINK_RE.sub("", text).strip()


def _knob() -> str:
    return os.environ.get("HIVE_LOCAL_INTERNAL", "auto").strip().lower()


async def internal_task_caller(
    capability: str,
    session_id: str,
    agent_id_prefix: str,
):
    """Return a (caller, label) pair for an internal meta-task.

    label is "local:<model>+haiku-fallback" or "claude:haiku" — callers
    log it so the E6 report can say which engine actually did the work.
    """
    from backend.llm.haiku import HaikuCaller
    from backend.workers.claude_cli import ClaudeCLIWorker

    haiku = HaikuCaller(worker=ClaudeCLIWorker(), session_id=session_id,
                        agent_id_prefix=agent_id_prefix)
    if _knob() == "off":
        return haiku, "claude:haiku"
    if capability == "distillation" and _knob() != "on":
        # E4.3 verdict (docs/E4_LOCAL_QUALITY_COMPARISON.md): qwen3:8b's
        # lesson draft invented a mechanism the evidence never showed —
        # and a local distiller would also run the groundedness GATE
        # that is supposed to catch its own confabulations. Distillation
        # is low-volume (~$0.03/session close), the risk asymmetry is
        # large, so it stays on Haiku unless explicitly forced with
        # HIVE_LOCAL_INTERNAL=on.
        return haiku, "claude:haiku"

    try:
        from backend.models_local import best_local_for, discover_local_models

        pool = await discover_local_models()
        match = best_local_for(capability, pool)
    except Exception:  # noqa: BLE001
        match = None
    if match is None:
        return haiku, "claude:haiku"

    local = LocalCaller(model=match.name, session_id=session_id,
                        agent_id_prefix=agent_id_prefix)

    async def local_with_fallback(prompt: str) -> str:
        try:
            return await local(prompt)
        except Exception as exc:  # noqa: BLE001 — any local failure → Haiku
            logger.warning("Local %s call failed (%s) — falling back to Haiku",
                           match.name, exc)
            return await haiku(prompt)

    return local_with_fallback, f"local:{match.name}+haiku-fallback"
