"""Minimal VRAM manager (E1).

Answers one question honestly: "does a local model of size X fit on the
GPUs RIGHT NOW?" — live nvidia-smi readings plus an in-process reservation
ledger for local workers HIVE has spawned but whose weights may not have
loaded yet (the race between two concurrent spawns both seeing the same
free VRAM).

Deliberately NOT a scheduler: Ollama owns model placement across GPUs and
eviction. We only gate spawns. Simplification (documented): headroom is
summed across GPUs because Ollama splits large models across cards; a
model that only *barely* fits in the sum may still thrash — the 85%
utilization guard keeps a margin for that.

Degradation: no nvidia-smi (non-NVIDIA box, driver hiccup) → snapshot()
returns None and callers treat VRAM as unknown-but-fine; the Ollama-side
failure mode is a slow model, not a crash, and hive doctor surfaces it.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_SMI_TIMEOUT_S = 4.0
# Fraction of total VRAM we allow to be planned-full. Above this, loading
# another model risks eviction thrash / host instability.
MAX_PLANNED_UTILIZATION = 0.85


@dataclass
class GPUInfo:
    index: int
    name: str
    total_mb: int
    used_mb: int

    @property
    def free_mb(self) -> int:
        return max(0, self.total_mb - self.used_mb)


@dataclass
class VRAMSnapshot:
    gpus: list[GPUInfo]
    reserved_mb: int = 0

    @property
    def total_mb(self) -> int:
        return sum(g.total_mb for g in self.gpus)

    @property
    def used_mb(self) -> int:
        return sum(g.used_mb for g in self.gpus)

    @property
    def headroom_mb(self) -> int:
        """Free VRAM minus outstanding reservations and the safety margin."""
        budget = int(self.total_mb * MAX_PLANNED_UTILIZATION)
        return max(0, budget - self.used_mb - self.reserved_mb)

    @property
    def used_percent(self) -> float:
        """Worst single GPU — feeds the safety hard-stop."""
        if not self.gpus:
            return 0.0
        return max(100.0 * g.used_mb / g.total_mb for g in self.gpus if g.total_mb)


class VRAMManager:
    """nvidia-smi reader + reservation ledger. One instance per process."""

    def __init__(self) -> None:
        self._reservations: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def snapshot(self) -> VRAMSnapshot | None:
        """Live GPU state, or None when VRAM is unknowable on this box."""
        gpus = await _query_nvidia_smi()
        if gpus is None:
            return None
        return VRAMSnapshot(gpus=gpus, reserved_mb=sum(self._reservations.values()))

    async def reserve(self, key: str, need_mb: int) -> bool:
        """Reserve VRAM for a local worker about to spawn.

        Returns False when the model does not fit current headroom.
        Unknown VRAM (no nvidia-smi) reserves optimistically — Ollama
        queues internally rather than crashing.
        """
        async with self._lock:
            snap = await self.snapshot()
            if snap is not None and need_mb > snap.headroom_mb:
                logger.info(
                    "VRAM reservation refused for %s: need %dMB, headroom %dMB",
                    key, need_mb, snap.headroom_mb)
                return False
            self._reservations[key] = need_mb
            return True

    def release(self, key: str) -> None:
        self._reservations.pop(key, None)


async def _query_nvidia_smi() -> list[GPUInfo] | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.used",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_SMI_TIMEOUT_S)
    except (FileNotFoundError, asyncio.TimeoutError, OSError) as exc:
        logger.debug("nvidia-smi unavailable: %s", exc)
        return None
    if proc.returncode != 0:
        return None
    gpus: list[GPUInfo] = []
    for line in stdout.decode(errors="replace").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        try:
            gpus.append(GPUInfo(index=int(parts[0]), name=parts[1],
                                total_mb=int(parts[2]), used_mb=int(parts[3])))
        except ValueError:
            continue
    return gpus or None


vram_manager = VRAMManager()
