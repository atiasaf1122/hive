"""NDJSON streaming pipeline for claude CLI --output-format stream-json.

Invariant from HIVE_BUILD_PLAN: always buffer chunks, split on \n,
parse each line as a separate JSON object. Never parse raw text output.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from backend.workers.base import EventType, HiveEvent, WorkerConfig

logger = logging.getLogger(__name__)


async def parse_stream(
    stdout: asyncio.StreamReader,
    config: WorkerConfig,
) -> AsyncIterator[HiveEvent]:
    """Read raw bytes from claude CLI stdout, yield HiveEvents.

    Handles partial chunks: accumulates a byte buffer and splits on newlines.
    Each complete line is parsed as one JSON event.
    """
    buf = b""

    while True:
        chunk = await stdout.read(4096)
        if not chunk:
            break
        buf += chunk

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

    # assistant message with content blocks (text + tool_use)
    if ptype == "assistant":
        message = payload.get("message", {})
        events: list[HiveEvent] = []
        for block in message.get("content", []):
            btype = block.get("type", "")
            if btype == "text":
                events.append(HiveEvent(
                    type=EventType.TEXT_DELTA,
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
