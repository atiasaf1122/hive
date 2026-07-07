"""Local (Ollama) model discovery + three-layer capability resolver.

Post-1.0 Part 2 upgrade — the static family map became a resolver that
knows every model Ollama has, including families that don't exist yet:

1. **Measured** (audition): `hive models audition <model>` runs fixed $0
   micro-tasks (code+pytest, summary graded by Haiku, classification
   exact-match) and stores the results. Measured capabilities OVERRIDE
   everything else.
2. **Metadata** (/api/show): family/families, parameter_size,
   quantization and the capabilities array, cached in SQLite per
   (model, digest) so we probe once per model VERSION, not per plan.
3. **Inference** (name+size patterns): one rules table — *coder*/*code*
   families are coding-capable, size tiers decide how far to trust them
   (<7B → classification/summarization; 7–14B coder → light coding;
   ≥14B coder → full coding worker). A future family ("qwen5-coder:40b")
   resolves sensibly by pattern+size. Unknown family + no signals →
   conservative defaults, as before.

A model is *available* only when it is pulled in Ollama AND its estimated
VRAM need fits current headroom. If Ollama is down, discovery returns an
empty pool and every caller silently falls back to Claude-only.

Discovery NEVER raises and never blocks on the probe cache — any probe
failure degrades to the inference layer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Runtime VRAM ≈ weights * 1.15 (runner overhead) + ~1.5GB KV/context.
# Rough by design — the 85% utilization guard in resources.py absorbs the
# estimation error.
_VRAM_OVERHEAD_FACTOR = 1.15
_VRAM_CONTEXT_MB = 1500

# Below this size a model is a classifier, not a colleague.
_SMALL_MODEL_GB = 4.5

# ── Layer 3: inference rules — ONE table, first match wins ───────────────────
# A row matches when the name/family matches `pattern` (None = any) AND the
# parameter count is >= min_params_b. Extend by adding rows.
_CODER_RE = re.compile(r"cod(?:e|er|ing)|devstral|codestral|starcoder", re.I)
_GENERALIST_RE = re.compile(
    r"qwen|llama|mistral|mixtral|gemma|phi|deepseek|granite|smollm|command-r",
    re.I,
)

_INFERENCE_RULES: list[tuple[re.Pattern[str] | None, float, frozenset[str], str]] = [
    (_CODER_RE, 14.0,
     frozenset({"coding", "light_coding", "summarization", "classification"}),
     "haiku-to-sonnet for mechanical coding (≥14B coder)"),
    (_CODER_RE, 7.0,
     frozenset({"light_coding", "summarization", "classification"}),
     "light coding only (7–14B coder)"),
    (_GENERALIST_RE, 7.0,
     frozenset({"summarization", "distillation", "classification"}),
     "haiku for text/meta tasks"),
    (None, 7.0,
     frozenset({"summarization", "classification"}),
     "unknown family — meta tasks only"),
    (None, 0.0,
     frozenset({"classification", "summarization"}),
     "small model — classification/short summaries only"),
]

_PARAM_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([bm])\b", re.I)


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
    # Part 2: where the capabilities came from — "measured" (audition) |
    # "inferred" (metadata or name/size patterns) | "default" (conservative).
    provenance: str = "default"

    def as_dict(self) -> dict:
        return {
            "name": self.name, "size_gb": round(self.size_gb, 1),
            "capabilities": sorted(self.capabilities),
            "tier_equivalence": self.tier_equivalence,
            "vram_need_mb": self.vram_need_mb,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "resident": self.resident,
            "provenance": self.provenance,
        }


# ── parameter-count estimation ────────────────────────────────────────────────

def _parse_params_b(text: str) -> float | None:
    """'30.5B' → 30.5, '780M' → 0.78; last size token wins ('llama3.2:3b' → 3)."""
    matches = _PARAM_TOKEN_RE.findall(text or "")
    if not matches:
        return None
    value, unit = matches[-1]
    n = float(value)
    return n / 1000.0 if unit.lower() == "m" else n


def estimate_params_b(name: str, size_gb: float, metadata: dict | None = None) -> float:
    """Best-effort parameter count in billions: metadata > name tag > disk size."""
    meta_params = _parse_params_b((metadata or {}).get("parameter_size", ""))
    if meta_params:
        return meta_params
    name_params = _parse_params_b(name)
    if name_params:
        return name_params
    # q4-ish heuristic: weights bytes ≈ params × ~0.6 bytes.
    return round(size_gb / 0.6, 1) if size_gb else 0.0


# ── capability resolution (all three layers) ─────────────────────────────────

def _resolve_by_rules(haystack: str, params_b: float) -> tuple[frozenset[str], str, bool]:
    """Apply the inference table. Returns (caps, tier, matched_known_pattern)."""
    known = bool(_CODER_RE.search(haystack) or _GENERALIST_RE.search(haystack))
    for pattern, min_b, caps, tier in _INFERENCE_RULES:
        if pattern is not None and not pattern.search(haystack):
            continue
        if params_b >= min_b:
            return caps, tier, known
    return _INFERENCE_RULES[-1][2], _INFERENCE_RULES[-1][3], known


def resolve_model(
    name: str,
    size_gb: float,
    metadata: dict | None = None,
    measured: dict | None = None,
) -> tuple[frozenset[str], str, str]:
    """Full three-layer resolution → (capabilities, tier, provenance)."""
    # Layer 1 — measured audition results override everything.
    measured_caps = (measured or {}).get("capabilities")
    if measured_caps is not None:
        caps = frozenset(measured_caps) or frozenset({"classification"})
        return caps, "audition-measured (see hive models audition)", "measured"

    # Layers 2+3 — metadata feeds the same rules (family names count as
    # signals), then name+size patterns.
    meta = metadata or {}
    family_bits = " ".join(
        [str(meta.get("family") or ""), *(meta.get("families") or [])])
    haystack = f"{name} {family_bits}".lower()
    params_b = estimate_params_b(name, size_gb, meta)
    caps, tier, known = _resolve_by_rules(haystack, params_b)

    # Ollama-reported vision support is worth surfacing (informational —
    # local workers still have no tool loop, so no "tools" capability).
    if "vision" in (meta.get("capabilities") or []):
        caps = caps | frozenset({"vision"})

    provenance = "inferred" if (known or meta.get("family")) else "default"
    return caps, tier, provenance


def resolve_capabilities(name: str, size_gb: float) -> tuple[frozenset[str], str]:
    """Back-compat wrapper: inference layers only (no metadata/measured)."""
    caps, tier, _ = resolve_model(name, size_gb)
    return caps, tier


def estimate_vram_mb(size_gb: float) -> int:
    return int(size_gb * 1024 * _VRAM_OVERHEAD_FACTOR) + _VRAM_CONTEXT_MB


# ── Layer 2: /api/show metadata + SQLite probe cache ─────────────────────────

def _extract_show_metadata(payload: dict) -> dict:
    details = payload.get("details") or {}
    return {
        "family": str(details.get("family") or ""),
        "families": [str(f) for f in (details.get("families") or [])],
        "parameter_size": str(details.get("parameter_size") or ""),
        "quantization": str(details.get("quantization_level") or ""),
        "capabilities": [str(c) for c in (payload.get("capabilities") or [])],
    }


async def _get_probe(model: str, digest: str, db_path: Path | None = None) -> dict | None:
    """Cached probe row → {"metadata": dict, "measured": dict|None} or None."""
    from backend.persistence.db import DB_PATH, get_conn
    try:
        async with get_conn(db_path or DB_PATH) as conn:
            async with conn.execute(
                "SELECT metadata_json, measured_json FROM local_model_probes "
                "WHERE model = ? AND digest = ?", (model, digest),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return {
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "measured": json.loads(row["measured_json"]) if row["measured_json"] else None,
        }
    except Exception as exc:  # noqa: BLE001 — cache is best-effort
        logger.debug("Probe cache read failed for %s: %s", model, exc)
        return None


async def _store_probe(model: str, digest: str, metadata: dict,
                       db_path: Path | None = None) -> None:
    from backend.persistence.db import DB_PATH, get_conn
    try:
        async with get_conn(db_path or DB_PATH) as conn:
            await conn.execute(
                "INSERT INTO local_model_probes (model, digest, metadata_json) "
                "VALUES (?,?,?) ON CONFLICT(model, digest) "
                "DO UPDATE SET metadata_json = excluded.metadata_json",
                (model, digest, json.dumps(metadata)),
            )
            await conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Probe cache write failed for %s: %s", model, exc)


async def record_measured(model: str, digest: str, measured: dict,
                          db_path: Path | None = None) -> None:
    """Store audition results (upserts the row if discovery never ran)."""
    from backend.persistence.db import DB_PATH, get_conn
    async with get_conn(db_path or DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO local_model_probes (model, digest, measured_json, measured_at) "
            "VALUES (?,?,?,datetime('now')) ON CONFLICT(model, digest) "
            "DO UPDATE SET measured_json = excluded.measured_json, "
            "measured_at = datetime('now')",
            (model, digest, json.dumps(measured)),
        )
        await conn.commit()


async def unauditioned_models(db_path: Path | None = None) -> list[dict]:
    """Probe rows never auditioned — the 'new local model detected' nudge."""
    from backend.persistence.db import DB_PATH, get_conn
    try:
        async with get_conn(db_path or DB_PATH) as conn:
            async with conn.execute(
                "SELECT model, digest, discovered_at FROM local_model_probes "
                "WHERE measured_json IS NULL ORDER BY discovered_at DESC",
            ) as cur:
                rows = await cur.fetchall()
        return [{"model": r["model"], "digest": r["digest"],
                 "discovered_at": r["discovered_at"]} for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.debug("Nudge query failed: %s", exc)
        return []


# ── discovery ─────────────────────────────────────────────────────────────────

async def discover_local_models(
    base_url: str | None = None,
    session_id: str = "",
) -> list[LocalModel]:
    """Pulled models with three-layer capability resolution and VRAM gating.

    Returns [] when Ollama is unreachable — the Claude-only degradation
    path. Never raises. New (model, digest) pairs are probed via /api/show
    once, cached, and emit MODEL_DISCOVERED (when a session_id is at hand).
    """
    from backend.detection import resolved_ollama_base
    from backend.resources import vram_manager

    base = base_url or resolved_ollama_base()
    probes: dict[str, tuple[dict | None, dict | None, bool]] = {}
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

            # Layer-2 probe: once per (model, digest); everything guarded so
            # discovery survives cache/HTTP failures on the inference layer.
            for tag in tags:
                name = str(tag.get("name") or "").strip()
                if not name:
                    continue
                digest = str(tag.get("digest") or "")
                try:
                    cached = await _get_probe(name, digest)
                    if cached is not None:
                        probes[name] = (cached["metadata"], cached["measured"], False)
                        continue
                    show = await client.post(f"{base}/api/show",
                                             json={"model": name})
                    show.raise_for_status()
                    metadata = _extract_show_metadata(show.json())
                    await _store_probe(name, digest, metadata)
                    probes[name] = (metadata, None, True)  # newly discovered
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Probe failed for %s: %s", name, exc)
                    probes[name] = (None, None, False)
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
        metadata, measured, is_new = probes.get(name) or (None, None, False)
        caps, tier, provenance = resolve_model(name, size_gb, metadata, measured)
        need_mb = estimate_vram_mb(size_gb)
        model = LocalModel(name=name, size_gb=size_gb, capabilities=caps,
                           tier_equivalence=tier, vram_need_mb=need_mb,
                           resident=name in resident, provenance=provenance)
        if model.resident or snap is None:
            # Resident → already paid for; unknowable VRAM → don't block.
            model.available = True
        elif need_mb <= snap.headroom_mb:
            model.available = True
        else:
            model.unavailable_reason = (
                f"needs ~{need_mb}MB VRAM, headroom {snap.headroom_mb}MB")
        models.append(model)

        if is_new and session_id:
            try:
                from backend.persistence.events import write_event
                from backend.workers.base import EventType, HiveEvent
                await write_event(HiveEvent(
                    type=EventType.MODEL_DISCOVERED, agent_id="discovery",
                    session_id=session_id,
                    raw_payload={"model": name, "size_gb": round(size_gb, 1),
                                 "provenance": provenance},
                ))
            except Exception as exc:  # noqa: BLE001
                logger.debug("MODEL_DISCOVERED write failed: %s", exc)
    return models


def best_local_for(capability: str, models: list[LocalModel]) -> LocalModel | None:
    """Largest available model carrying the capability (size ~ quality).
    Measured beats inferred beats default at equal capability."""
    fits = [m for m in models if m.available and capability in m.capabilities]
    rank = {"measured": 2, "inferred": 1, "default": 0}
    return max(fits, key=lambda m: (rank.get(m.provenance, 0), m.size_gb)) if fits else None


# ── Layer 1: audition (measured, not guessed) ────────────────────────────────

_AUDITION_CLASSIFY: list[tuple[str, str]] = [
    ("The login page crashes with a 500 when I submit an empty form", "bug"),
    ("Please add dark mode support to the settings screen", "feature"),
    ("How does the caching layer decide what to evict?", "question"),
    ("Update the README with the new install steps", "docs"),
    ("Split the giant utils.py into focused modules without changing behavior", "refactor"),
]
_AUDITION_CLASSIFY_PROMPT = (
    "Classify this software request as exactly one word from this list: "
    "bug, feature, question, docs, refactor.\n"
    "Reply with ONLY that single word, lowercase, nothing else.\n\n"
    "Request: {text}"
)

_AUDITION_DOC = (
    "The scheduler runs nightly at 02:00 and scans the jobs table for rows "
    "whose next_run is in the past. Each due job is dispatched to a worker "
    "pool with a concurrency limit of four; jobs that raise are retried "
    "twice with exponential backoff before being marked failed and alerting "
    "the on-call channel. Completed jobs write a duration metric and "
    "reschedule themselves by adding their interval to the previous "
    "next_run, not to now, so drift does not accumulate. A watchdog thread "
    "kills any job that exceeds its per-job timeout, which defaults to ten "
    "minutes but can be overridden per job. Metrics are exported to "
    "Prometheus and a weekly report aggregates failure rates by job type."
)
_AUDITION_SUMMARIZE_PROMPT = (
    "Summarize the following document in at most 3 sentences. Reply with "
    "ONLY the summary.\n\n---\n{doc}\n---"
)
_AUDITION_GRADE_PROMPT = (
    "Grade this summary of the reference document on a 0-10 scale for "
    "accuracy and coverage. Reply with ONLY the integer.\n\n"
    "Reference document:\n{doc}\n\nSummary to grade:\n{summary}"
)

_AUDITION_CODE_PROMPT = (
    "Write Python code: a function `median_of(nums)` that returns the "
    "median of a non-empty list of numbers (average of the two middle "
    "values for even lengths), plus pytest tests named test_* covering an "
    "odd-length list, an even-length list, and a single-element list. "
    "Reply with ONLY one ```python code block containing both the function "
    "and the tests, no prose."
)

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.S)


async def _generate(base: str, model: str, prompt: str, timeout_s: float = 120.0) -> str:
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(f"{base}/api/generate",
                                 json={"model": model, "prompt": prompt,
                                       "stream": False})
        resp.raise_for_status()
        return str(resp.json().get("response") or "")


def _run_pytest_on(code_text: str) -> bool:
    """Extract the fenced block, write it to a temp file, run pytest on it."""
    m = _CODE_BLOCK_RE.search(code_text)
    body = m.group(1) if m else code_text  # tolerate blockless replies
    if "def median_of" not in body or "def test_" not in body:
        return False
    with tempfile.TemporaryDirectory(prefix="hive-audition-") as td:
        target = Path(td) / "test_audition.py"
        target.write_text(body, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", str(target)],
                cwd=td, capture_output=True, timeout=60,
            )
            return proc.returncode == 0
        except Exception:  # noqa: BLE001 — timeout/env failure = not passed
            return False


async def _haiku_grade(doc: str, summary: str) -> int:
    """Grade a summary 0-10 via Haiku. Raises on an ungradable reply."""
    from backend.orchestrator.task_router import _haiku
    raw = await _haiku(_AUDITION_GRADE_PROMPT.format(doc=doc, summary=summary))
    m = re.search(r"\d+", raw or "")
    if not m:
        raise ValueError(f"ungradable Haiku reply: {raw!r}")
    return max(0, min(10, int(m.group(0))))


async def audition_model(
    name: str,
    base_url: str | None = None,
    db_path: Path | None = None,
    grader=None,
    generate=None,
) -> dict:
    """Run the fixed micro-tasks against a local model and store MEASURED
    capabilities (overriding inference). $0 except one tiny Haiku grade.

    `grader`/`generate` are injectable for tests.
    """
    from backend.detection import resolved_ollama_base

    base = base_url or resolved_ollama_base()
    gen = generate or (lambda prompt: _generate(base, name, prompt))
    grade = grader or _haiku_grade

    results: dict[str, dict] = {}

    # 1. classification — exact match on 5 fixed requests.
    hits = 0
    for text, expected in _AUDITION_CLASSIFY:
        out = await gen(_AUDITION_CLASSIFY_PROMPT.format(text=text))
        first = (out or "").strip().strip(".").split()
        if first and first[0].lower() == expected:
            hits += 1
    results["classification"] = {"score": hits, "max": 5, "passed": hits >= 4}

    # 2. summarization — graded 0-10 by Haiku.
    summary = await gen(_AUDITION_SUMMARIZE_PROMPT.format(doc=_AUDITION_DOC))
    try:
        score = await grade(_AUDITION_DOC, summary)
    except Exception as exc:  # noqa: BLE001 — grader down ≠ model failed
        logger.warning("Audition grader unavailable (%s) — skipping summary task", exc)
        score = None
    results["summarization"] = {"score": score, "max": 10,
                                "passed": bool(score is not None and score >= 6)}

    # 3. coding — generated function+tests must pass pytest.
    code_out = await gen(_AUDITION_CODE_PROMPT)
    code_ok = await asyncio.to_thread(_run_pytest_on, code_out)
    results["coding"] = {"passed": code_ok}

    caps: set[str] = set()
    if results["classification"]["passed"]:
        caps.add("classification")
    if results["summarization"]["passed"]:
        caps |= {"summarization", "distillation"}
    if results["coding"]["passed"]:
        caps |= {"coding", "light_coding"}

    measured = {"capabilities": sorted(caps), "results": results,
                "at": time.time()}

    digest = ""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(f"{base}/api/tags")
            resp.raise_for_status()
            for tag in resp.json().get("models") or []:
                if str(tag.get("name") or "").strip() == name:
                    digest = str(tag.get("digest") or "")
                    break
    except Exception as exc:  # noqa: BLE001 — digest is best-effort keying
        logger.debug("Digest lookup failed for %s: %s", name, exc)

    await record_measured(name, digest, measured, db_path)
    return measured
