"""Local (Ollama) model discovery + capability catalog (E1).

A small curated map from known model FAMILIES to what they're honestly
good for, plus discovery that combines the live Ollama tag list with the
VRAM headroom from backend.resources. A dict and a resolver — not a
framework.

A model is *available* only when it is pulled in Ollama AND its estimated
VRAM need fits current headroom. Unknown pulled models get conservative
defaults (summarization/classification only) — better to under-promise
than to hand a 3B model a refactor.

If Ollama is down or nothing suitable is pulled, discovery returns an
empty pool and every caller (planner digest, distiller/summarizer
selection, task-shape classifier) silently falls back to Claude-only:
HIVE works identically without Ollama.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# Runtime VRAM ≈ weights * 1.15 (runner overhead) + ~1.5GB KV/context.
# Rough by design — the 85% utilization guard in resources.py absorbs the
# estimation error.
_VRAM_OVERHEAD_FACTOR = 1.15
_VRAM_CONTEXT_MB = 1500

# Ordered: first substring match wins. Curated for what's actually good,
# not exhaustive.
_FAMILY_MAP: list[tuple[tuple[str, ...], frozenset[str], str]] = [
    (("qwen3-coder", "qwen2.5-coder", "deepseek-coder", "codellama",
      "devstral", "codestral", "starcoder"),
     frozenset({"coding", "summarization", "classification"}),
     "haiku-to-sonnet for mechanical coding"),
    (("qwen3", "qwen2.5", "llama3", "mistral", "gemma", "phi"),
     frozenset({"summarization", "distillation", "classification"}),
     "haiku for text/meta tasks"),
]
_CONSERVATIVE = (frozenset({"summarization", "classification"}),
                 "unknown family — meta tasks only")

# Below this size a model is a classifier, not a colleague.
_SMALL_MODEL_GB = 4.5


@dataclass
class LocalModel:
    name: str                      # ollama tag, e.g. "qwen3-coder:30b"
    size_gb: float                 # download size from /api/tags
    capabilities: frozenset[str]
    tier_equivalence: str
    vram_need_mb: int
    available: bool = False
    unavailable_reason: str = ""
    # F0.4: already loaded in VRAM (from /api/ps) — needs NO new headroom.
    # Without this, a resident 19GB model was double-counted and marked
    # unavailable the moment its own residency ate the headroom.
    resident: bool = False

    def as_dict(self) -> dict:
        return {
            "name": self.name, "size_gb": round(self.size_gb, 1),
            "capabilities": sorted(self.capabilities),
            "tier_equivalence": self.tier_equivalence,
            "vram_need_mb": self.vram_need_mb,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "resident": self.resident,
        }


def resolve_capabilities(name: str, size_gb: float) -> tuple[frozenset[str], str]:
    """Family lookup with a small-model downgrade. Pure function."""
    base = name.lower()
    caps, tier = _CONSERVATIVE
    for patterns, family_caps, family_tier in _FAMILY_MAP:
        if any(p in base for p in patterns):
            caps, tier = family_caps, family_tier
            break
    if size_gb and size_gb < _SMALL_MODEL_GB:
        # Small models classify fine; everything else degrades sharply.
        caps = caps & frozenset({"classification", "summarization"}) | frozenset({"classification"})
        tier = "small model — classification/short summaries only"
    return caps, tier


def estimate_vram_mb(size_gb: float) -> int:
    return int(size_gb * 1024 * _VRAM_OVERHEAD_FACTOR) + _VRAM_CONTEXT_MB


async def discover_local_models(base_url: str | None = None) -> list[LocalModel]:
    """Pulled models with capability tags and VRAM-gated availability.

    Returns [] when Ollama is unreachable — the Claude-only degradation
    path. Never raises.
    """
    from backend.detection import resolved_ollama_base
    from backend.resources import vram_manager

    base = base_url or resolved_ollama_base()
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{base}/api/tags")
            resp.raise_for_status()
            tags = resp.json().get("models") or []
            # F0.4: /api/ps lists models currently loaded in VRAM — a
            # resident model needs no new headroom (it was being
            # double-counted and marked unavailable by its own residency).
            resident: set[str] = set()
            try:
                ps = await client.get(f"{base}/api/ps")
                ps.raise_for_status()
                resident = {str(m.get("name") or "").strip()
                            for m in (ps.json().get("models") or [])}
            except Exception as exc:  # noqa: BLE001 — residency is a bonus
                logger.debug("Ollama /api/ps failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 — any failure means "no local pool"
        logger.debug("Ollama discovery failed at %s: %s", base, exc)
        return []

    snap = await vram_manager.snapshot()
    models: list[LocalModel] = []
    for tag in tags:
        name = str(tag.get("name") or "").strip()
        if not name:
            continue
        size_gb = float(tag.get("size") or 0) / 1e9
        caps, tier = resolve_capabilities(name, size_gb)
        need_mb = estimate_vram_mb(size_gb)
        model = LocalModel(name=name, size_gb=size_gb, capabilities=caps,
                           tier_equivalence=tier, vram_need_mb=need_mb,
                           resident=name in resident)
        if model.resident or snap is None:
            # Resident → already paid for; unknowable VRAM → don't block.
            model.available = True
        elif need_mb <= snap.headroom_mb:
            model.available = True
        else:
            model.unavailable_reason = (
                f"needs ~{need_mb}MB VRAM, headroom {snap.headroom_mb}MB")
        models.append(model)
    return models


def best_local_for(capability: str, models: list[LocalModel]) -> LocalModel | None:
    """Largest available model carrying the capability (size ~ quality)."""
    fits = [m for m in models if m.available and capability in m.capabilities]
    return max(fits, key=lambda m: m.size_gb) if fits else None
