"""MCP doctor — prove a server actually starts before an agent pays for it.

Phase C burned three full swarm runs on servers that crashed at startup
(invalid flag combo, missing browser channel, mismatched browser build);
every one of those would have been caught by the raw-stdio initialize
probe this module productizes (D0.3).

For stdio servers: spawn the real command, complete an MCP `initialize`
handshake, tear the process group down. For http servers: POST the same
initialize request. Results are cached per (server id, args hash) for the
backend's lifetime — the first agent of a session pays ~1-3s, later spawns
are free.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import tempfile

from backend.mcp.catalog import (
    MCPServerSpec,
    _expand_env_map,
    _expand_placeholders,
)

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[bool, str]] = {}

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "hive-doctor", "version": "1"},
    },
}


def _cache_key(spec: MCPServerSpec, args: list[str]) -> str:
    digest = hashlib.sha256(" ".join(args).encode()).hexdigest()[:16]
    return f"{spec.id}:{digest}"


async def check_server(
    spec: MCPServerSpec,
    *,
    agent_id: str = "doctor",
    worktree: str | None = None,
    timeout: float = 10.0,
    use_cache: bool = True,
) -> tuple[bool, str]:
    """Return (ok, detail). detail carries stderr/status text on failure."""
    worktree = worktree or tempfile.gettempdir()
    args = [
        _expand_placeholders(a, agent_id, worktree)
        for a in (*spec.args, *spec.isolation_args)
    ]
    key = _cache_key(spec, args)
    if use_cache and key in _CACHE:
        return _CACHE[key]

    if spec.transport == "http":
        result = await _check_http(spec, agent_id, worktree, timeout)
    else:
        result = await _check_stdio(spec, args, timeout)

    _CACHE[key] = result
    return result


def clear_cache() -> None:
    _CACHE.clear()


async def _check_stdio(
    spec: MCPServerSpec, args: list[str], timeout: float
) -> tuple[bool, str]:
    try:
        env = {**os.environ, **_expand_env_map(spec.env)}
    except ValueError as exc:
        return False, str(exc)

    try:
        proc = await asyncio.create_subprocess_exec(
            spec.command, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        return False, f"could not spawn {spec.command}: {exc}"

    async def _handshake() -> tuple[bool, str]:
        assert proc.stdin and proc.stdout
        proc.stdin.write((json.dumps(_INITIALIZE) + "\n").encode())
        await proc.stdin.drain()
        while True:
            line = await proc.stdout.readline()
            if not line:
                stderr = (await proc.stderr.read(2048)).decode(errors="replace")  # type: ignore[union-attr]
                return False, f"server exited before responding: {stderr.strip()[:500]}"
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 1:
                if "result" in msg:
                    name = (msg["result"].get("serverInfo") or {}).get("name", spec.id)
                    return True, f"initialize OK ({name})"
                return False, f"initialize error: {json.dumps(msg.get('error'))[:300]}"

    try:
        return await asyncio.wait_for(_handshake(), timeout=timeout)
    except asyncio.TimeoutError:
        stderr = b""
        try:
            stderr = await asyncio.wait_for(proc.stderr.read(2048), timeout=1.0)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        return False, (
            f"no initialize response within {timeout:.0f}s"
            + (f"; stderr: {stderr.decode(errors='replace').strip()[:400]}" if stderr else "")
        )
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


async def _check_http(
    spec: MCPServerSpec, agent_id: str, worktree: str, timeout: float
) -> tuple[bool, str]:
    try:
        headers = _expand_env_map(spec.headers)
    except ValueError as exc:
        return False, str(exc)
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json, text/event-stream")
    url = _expand_placeholders(spec.url, agent_id, worktree)

    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=_INITIALIZE, headers=headers)
        if resp.status_code < 500:
            return True, f"endpoint reachable (HTTP {resp.status_code})"
        return False, f"endpoint returned HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"endpoint unreachable: {exc}"
