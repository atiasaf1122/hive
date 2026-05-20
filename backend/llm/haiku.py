"""Session-scoped Haiku one-shot caller with budget enforcement.

Three places already accept a `haiku_caller=` injection point:

  - `backend.validation.validators.semantic_cross_check`
  - `backend.skills.registry.maybe_rerank`
  - (soon) `backend.summarizer.run_summarizer` from Item 3

`HaikuCaller` is the production implementation behind all of them. It
wraps `ClaudeCLIWorker` (or any other `Worker`) and:

  1. Spawns a single-turn run with `--model claude-haiku-4-5`.
  2. Collects TEXT_DELTA events into a final response string.
  3. Records token + USD cost via `backend.persistence.events.write_cost`
     so the existing /api/cost dashboard picks it up automatically.
  4. Enforces a per-session token budget; once exhausted, every further
     call raises `HaikuBudgetExhausted` until the budget is reset or the
     session ends. The cap is intentionally generous (default 50 000
     tokens / session) — the cost discipline invariant only matters
     when we'd otherwise leak Haiku calls.

The caller is `Callable[[str], Awaitable[str]]`-compatible so it slots
into the existing `haiku_caller=` parameters without any further wiring.

Failure modes are surfaced as exceptions; the call sites already wrap
the call in try/except and fall back to the cheaper path on error
(see validators.semantic_cross_check and registry.maybe_rerank).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol, runtime_checkable

from backend.workers.base import EventType, HiveEvent, WorkerConfig

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_TOKENS = int(os.environ.get("HIVE_HAIKU_BUDGET_TOKENS", "50000"))
DEFAULT_MAX_RESPONSE_LEN = 8_000  # characters; Haiku rarely needs more for our prompts


class HaikuBudgetExhausted(RuntimeError):
    """Raised when a session has burned through its Haiku token budget."""


@runtime_checkable
class _MinimalWorker(Protocol):
    """Just the slice of Worker we need — run + kill."""

    async def run(self, prompt: str, config: WorkerConfig): ...  # AsyncIterator[HiveEvent]
    async def kill(self, agent_id: str) -> None: ...


@dataclass
class _Spend:
    """Running counters per session."""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0


@dataclass
class HaikuCaller:
    """One-shot Haiku caller scoped to a session.

    Instantiate once per session and pass `caller.invoke` (or just
    `caller` — it's callable) to any function that takes a
    `haiku_caller=` parameter.
    """

    worker: _MinimalWorker
    session_id: str
    budget_tokens: int = DEFAULT_BUDGET_TOKENS
    cost_writer: Callable[..., Awaitable[None]] | None = None  # injected for tests
    agent_id_prefix: str = "haiku"
    max_response_len: int = DEFAULT_MAX_RESPONSE_LEN
    worktree_path: str = "/tmp"   # one-shot calls don't write files
    _spend: _Spend = field(default_factory=_Spend)
    _call_counter: int = 0

    async def __call__(self, prompt: str) -> str:
        return await self.invoke(prompt)

    async def invoke(self, prompt: str) -> str:
        """Run a single Haiku turn and return the assistant's text."""
        self._check_budget()
        self._call_counter += 1
        agent_id = f"{self.agent_id_prefix}-{self.session_id}-{self._call_counter}"
        config = WorkerConfig(
            agent_id=agent_id,
            session_id=self.session_id,
            model="claude:haiku",
            worktree_path=self.worktree_path,
            max_turns=1,
        )

        chunks: list[str] = []
        last_cost: HiveEvent | None = None

        async for ev in self.worker.run(prompt, config):
            if ev.type in (EventType.TEXT_DELTA, EventType.TEXT_DELTA.value):
                if ev.text:
                    chunks.append(ev.text)
                    # Guardrail — Haiku occasionally rambles. We cap the
                    # collected output at max_response_len characters and
                    # kill the worker once the cap is hit.
                    if sum(len(c) for c in chunks) >= self.max_response_len:
                        await self.worker.kill(agent_id)
                        break
            elif ev.type in (EventType.COST, EventType.COST.value):
                last_cost = ev
            elif ev.type in (EventType.AGENT_ERROR, EventType.AGENT_ERROR.value):
                raise RuntimeError(f"Haiku call failed: {ev.error or '(no error msg)'}")

        if last_cost is not None:
            await self._record_cost(last_cost, agent_id)

        return "".join(chunks).strip()

    @property
    def spend(self) -> _Spend:
        return self._spend

    def remaining_tokens(self) -> int:
        used = self._spend.input_tokens + self._spend.output_tokens
        return max(0, self.budget_tokens - used)

    def _check_budget(self) -> None:
        if self.remaining_tokens() <= 0:
            raise HaikuBudgetExhausted(
                f"Haiku budget of {self.budget_tokens:,} tokens exhausted "
                f"for session {self.session_id}."
            )

    async def _record_cost(self, ev: HiveEvent, agent_id: str) -> None:
        inp = int(ev.input_tokens or 0)
        out = int(ev.output_tokens or 0)
        cost = float(ev.cost_usd or 0.0)
        self._spend.input_tokens += inp
        self._spend.output_tokens += out
        self._spend.cost_usd += cost
        self._spend.calls += 1

        writer = self.cost_writer
        if writer is None:
            # Late import — avoids pulling persistence into module-load
            # for unit tests that don't need it.
            from backend.persistence.events import write_cost
            writer = write_cost
        try:
            await writer(self.session_id, agent_id, inp, out, cost)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Haiku cost write failed for %s: %s", self.session_id, exc)


def build_caller(
    session_id: str,
    *,
    worker: _MinimalWorker | None = None,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    worktree_path: str = "/tmp",
) -> HaikuCaller:
    """Build a HaikuCaller backed by ClaudeCLIWorker by default.

    Tests can pass a stub `worker` that satisfies the `_MinimalWorker`
    protocol. Production paths leave it None and get the real CLI worker.
    """
    if worker is None:
        from backend.workers.claude_cli import ClaudeCLIWorker
        worker = ClaudeCLIWorker()
    return HaikuCaller(
        worker=worker,
        session_id=session_id,
        budget_tokens=budget_tokens,
        worktree_path=worktree_path,
    )
