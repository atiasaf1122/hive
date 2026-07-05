"""NDJSON streaming pipeline for claude CLI --output-format stream-json.

Invariant from HIVE_BUILD_PLAN: always buffer chunks, split on \n,
parse each line as a separate JSON object. Never parse raw text output.

Phase 10 hardening (Section 8.1): cap the buffer at MAX_BUFFER. A
single line longer than that is treated as corrupted — we look for the
next newline, drop everything up to it, and keep going. Without this,
a malformed upstream that never emits `\n` would let memory grow
unboundedly until OOM.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from backend.workers.base import EventType, HiveEvent, WorkerConfig

logger = logging.getLogger(__name__)

# 1 MB is comfortably above any sane single stream-json line — the claude
# CLI's biggest events (tool_result with large stdout) usually sit under
# 64 kB. Anything past 1 MB is corrupted, not real.
MAX_BUFFER = 1_048_576

# Stop reading from a stream that's been silent for this long. The env
# var lets ops dial it per-environment without redeploying.
import os as _os
IDLE_TIMEOUT_MS = int(_os.environ.get("CLAUDE_STREAM_IDLE_TIMEOUT_MS", "600000"))  # 10 min default


async def parse_stream(
    stdout: asyncio.StreamReader,
    config: WorkerConfig,
) -> AsyncIterator[HiveEvent]:
    """Read raw bytes from claude CLI stdout, yield HiveEvents.

    Handles partial chunks: accumulates a byte buffer and splits on
    newlines. Each complete line is parsed as one JSON event.
    Defensive against:

      - lines longer than MAX_BUFFER (treated as corrupt; recover from
        next newline)
      - non-JSON lines (logged at debug, dropped)
      - long silences (raises asyncio.TimeoutError so the worker can
        surface the stall instead of hanging forever)
    """
    buf = b""

    while True:
        try:
            chunk = await asyncio.wait_for(
                stdout.read(4096),
                timeout=IDLE_TIMEOUT_MS / 1000.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Stream parser: no output for %d ms — abandoning read.",
                IDLE_TIMEOUT_MS,
            )
            # Surface the stall as a failure. Previously this path silently
            # `return`ed, so a hung claude process ended the stream as if it
            # had finished and the turn falsely completed. The worker layer
            # recognises this error, kills the hung process, and skips its
            # own AGENT_END/exit-code handling.
            yield HiveEvent(
                type=EventType.AGENT_ERROR,
                agent_id=config.agent_id,
                session_id=config.session_id,
                error=(
                    f"worker idle-timeout after {IDLE_TIMEOUT_MS / 1000:.0f}s "
                    "— no output from claude CLI"
                ),
            )
            return
        if not chunk:
            # EOF — flush any complete lines left in the buffer before we
            # leave. Otherwise an event that arrived right before the
            # process closed (or that follows an overflow recovery) would
            # be silently dropped.
            for event in _flush_lines(buf, config):
                yield event
            break
        buf += chunk

        # Defensive: if the buffer balloons past MAX_BUFFER without a newline,
        # the upstream is malformed. Reset to the next newline if any,
        # otherwise drop everything and keep reading.
        if len(buf) > MAX_BUFFER:
            nl = buf.find(b"\n")
            if nl >= 0:
                dropped = nl + 1
                buf = buf[dropped:]
                logger.warning(
                    "Stream parser: dropped %d bytes (oversized line, recovered to next newline).",
                    dropped,
                )
            else:
                logger.warning(
                    "Stream parser: dropped %d bytes (no newline in oversized buffer).",
                    len(buf),
                )
                buf = b""
            continue

        while b"\n" in buf:
            raw_line, buf = buf.split(b"\n", 1)
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                logger.debug("Non-JSON line from claude CLI: %s", raw_line[:120])
                continue

            for event in _expand(payload, config):
                yield event


def _flush_lines(buf: bytes, config: WorkerConfig) -> list[HiveEvent]:
    """Parse every newline-terminated line in buf and return their events.

    Used at EOF to drain a final batch that survived an overflow-recovery
    `continue` step. We deliberately ignore any trailing partial line —
    it's incomplete by definition.
    """
    if not buf:
        return []
    out: list[HiveEvent] = []
    for raw_line in buf.split(b"\n"):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            logger.debug("Non-JSON line at EOF: %s", raw_line[:120])
            continue
        out.extend(_expand(payload, config))
    return out


def _expand(payload: dict, config: WorkerConfig) -> list[HiveEvent]:
    """Map one claude stream-json payload to zero or more HiveEvents."""
    ptype = payload.get("type", "")
    base = dict(agent_id=config.agent_id, session_id=config.session_id)

    # system/init -> agent started
    if ptype == "system":
        subtype = payload.get("subtype", "")
        if subtype == "init":
            return [HiveEvent(type=EventType.AGENT_START, **base)]
        if subtype == "api_retry":
            error_info = payload.get("error", {})
            error_msg = (
                error_info.get("message", "") if isinstance(error_info, dict) else str(error_info)
            )
            is_rate_limit = "rate_limit" in error_msg.lower() or "rate_limit" in str(
                payload.get("error_code", "")
            )
            if is_rate_limit:
                wait_ms = int(payload.get("wait_ms", 5000))
                return [HiveEvent(
                    type=EventType.RATE_LIMIT,
                    error=error_msg,
                    retry_after_ms=wait_ms,
                    raw_payload=payload,
                    **base,
                )]
        return []

    # assistant message with content blocks (text + tool_use).
    # The consolidated `assistant` message arrives AFTER the partial
    # `stream_event` deltas. Emitting both as TEXT_DELTA causes
    # consumers (planner, summariser) to concatenate the same text
    # twice — visible in the chat as duplicated paragraphs. So we
    # emit text blocks as TEXT_DONE here; partial chunks below stay
    # TEXT_DELTA. Consumers that want the canonical final text listen
    # for TEXT_DONE; consumers that stream listen for TEXT_DELTA.
    if ptype == "assistant":
        message = payload.get("message", {})
        events: list[HiveEvent] = []
        for block in message.get("content", []):
            btype = block.get("type", "")
            if btype == "text":
                events.append(HiveEvent(
                    type=EventType.TEXT_DONE,
                    text=block.get("text", ""),
                    **base,
                ))
            elif btype == "tool_use":
                events.append(HiveEvent(
                    type=EventType.TOOL_USE,
                    tool_name=block.get("name"),
                    tool_input=block.get("input"),
                    tool_use_id=block.get("id"),
                    **base,
                ))
        return events

    # tool result
    if ptype == "tool_result":
        return [HiveEvent(
            type=EventType.TOOL_RESULT,
            tool_use_id=payload.get("tool_use_id"),
            tool_result=payload.get("content"),
            tool_result_error=bool(payload.get("is_error", False)),
            raw_payload=payload,
            **base,
        )]

    # final result with usage/cost
    if ptype == "result":
        usage = payload.get("usage", {})
        return [HiveEvent(
            type=EventType.COST,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost_usd=payload.get("total_cost_usd"),
            raw_payload=payload,
            **base,
        )]

    # partial text deltas from stream_event
    if ptype == "stream_event":
        inner = payload.get("event", {})
        if inner.get("type") == "content_block_delta":
            delta = inner.get("delta", {})
            if delta.get("type") == "text_delta":
                return [HiveEvent(
                    type=EventType.TEXT_DELTA,
                    text=delta.get("text", ""),
                    **base,
                )]

    return []
