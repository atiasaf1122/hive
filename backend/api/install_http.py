"""Real install endpoints — Phase 9C testing fix #7 and #8.

Skill install:
    POST /api/registries/skills/install
        body: {id, url, source, name, description?, tags?}
    → downloads SKILL.md (if url contains one), validates the YAML
      frontmatter, writes to ~/.hive/skills/<slug>/SKILL.md, registers
      via existing import_skill().
    → returns {ok, skill_id, path, errors[]}

For Cookbook URLs (github.com/.../tree/main/skills/<name>) we GET the
raw SKILL.md at the conventional path. For ClawHub / community items
without a known SKILL.md path we synthesise one from the curated
metadata so the user gets a sane local file they can edit.

MCP install:
    POST /api/registries/mcp/install
        body: {id, name, install: {transport, package}, permissions[]}
    → reads ~/.claude.json (or ~/.claude/config.json), adds the entry to
      mcpServers, writes back. Does NOT shell out to `npm install -g`
      yet — that's a longer-running command the user runs manually
      (we show the exact command in the success response).
    → returns {ok, config_path, command, mcp_id}

    DELETE /api/registries/mcp/{mcp_id}
    → removes the entry from claude config.

Both endpoints are deliberately conservative: they perform exactly one
durable action and surface every error to the UI.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.skills.registry import SKILLS_ROOT, import_skill, list_skills

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/registries")

# Hosts the install endpoint is allowed to fetch from. The endpoint is
# reachable from the WebView origin (Tauri allowlist is intentionally
# wide), so without this list any rendered skill markdown could drive
# the backend to GET http://127.0.0.1:8765/... or any internal address.
_ALLOWED_INSTALL_HOSTS = frozenset({
    "github.com",
    "raw.githubusercontent.com",
    "clawhub.dev",
})


# ── Skill install ────────────────────────────────────────────────────────────

class SkillInstallRequest(BaseModel):
    id: str
    name: str
    description: str
    source: str
    url: str | None = None
    tags: list[str] = []


@router.post("/skills/install")
async def skill_install(req: SkillInstallRequest) -> dict:
    """Materialise a SKILL.md for the chosen entry, then import it."""
    slug = _safe_slug(req.name or req.id)
    target_dir = SKILLS_ROOT / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "SKILL.md"

    body = await _fetch_skill_body(req)
    target.write_text(body, encoding="utf-8")

    try:
        skill = await import_skill(target)
    except Exception as exc:
        # Validation failed — undo to avoid leaving a half-installed skill.
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise HTTPException(
            status_code=400,
            detail=f"Skill validation failed: {exc}",
        ) from exc

    return {
        "ok": True,
        "skill_id": skill.id,
        "path": str(target),
        "source": req.source,
    }


async def _fetch_skill_body(req: SkillInstallRequest) -> str:
    """Best-effort: fetch the upstream SKILL.md. Synthesise if unreachable."""
    raw_url = _to_raw_url(req.url) if req.url else None
    if raw_url:
        # Policy check happens before any network I/O so a bad URL never
        # reaches httpx and never produces a network-side artifact.
        _validate_install_url(raw_url)
        try:
            async with httpx.AsyncClient(
                timeout=8.0, headers={"User-Agent": "HIVE-installer/1.0"}
            ) as client:
                resp = await client.get(raw_url)
                if resp.status_code == 200 and resp.text.startswith("---"):
                    return resp.text
                logger.info(
                    "Skill body fetch returned %s for %s — synthesising",
                    resp.status_code, raw_url,
                )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.info("Skill body fetch failed for %s: %s — synthesising", raw_url, exc)

    # Synthesised SKILL.md from curated metadata.
    tags_json = json.dumps(req.tags)
    return (
        "---\n"
        f"name: {req.name}\n"
        f"description: {req.description}\n"
        f"tags: {tags_json}\n"
        f"version: 1\n"
        "---\n\n"
        "## Instructions\n\n"
        f"{req.description}\n\n"
        f"_Installed from {req.source}. "
        "Edit this file to refine the instructions for your workflows._\n"
    )


def _to_raw_url(url: str) -> str | None:
    """Heuristic: turn a `github.com/<o>/<r>/tree/<ref>/<path>` URL into the
    raw SKILL.md URL when possible. For non-GitHub URLs we just return it."""
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.+)$", url)
    if m:
        owner, repo, ref, path = m.groups()
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}/SKILL.md"
    return url


def _safe_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-") or "untitled"


def _validate_install_url(url: str) -> None:
    """Raise HTTPException unless `url` targets an allowlisted public host.

    Two layers of defence:
      1. Hostname must be in `_ALLOWED_INSTALL_HOSTS` exactly.
      2. Every resolved IP must be public — guards against an allowlisted
         host pointing (via DNS rebinding or a typo'd CNAME) at a loopback
         / private / link-local address inside the user's network.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid install URL: {exc}") from exc

    if parsed.scheme != "https":
        raise HTTPException(
            400, f"Install URL must use https (got scheme: {parsed.scheme!r})"
        )

    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_INSTALL_HOSTS:
        raise HTTPException(
            400,
            f"Install host {host!r} is not allowed. "
            f"Permitted hosts: {sorted(_ALLOWED_INSTALL_HOSTS)}",
        )

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise HTTPException(
            502, f"Install host {host!r} did not resolve: {exc}"
        ) from exc

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise HTTPException(
                400,
                f"Install host {host!r} resolves to a non-public address ({ip}); refusing.",
            )


# ── Installed listings ────────────────────────────────────────────────────────
# The Skills/Plugins pages' "Installed" views used to live only in React
# state and reset on every reload (the "thin endpoint in 9D" that never
# shipped). These are those endpoints. IDs are name slugs — the same
# _slugify/_safe_slug transform both install paths use — so the frontend
# can match registry search items by slugifying item.name.


@router.get("/skills/installed")
async def skills_installed() -> dict:
    """List skills registered in the local registry (~/.hive/skills + DB)."""
    skills = await list_skills()
    return {
        "items": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "tags": s.tags,
                "version": s.version,
            }
            for s in skills
        ]
    }


@router.get("/mcp/installed")
async def mcp_installed() -> dict:
    """List MCP servers configured in the user's Claude config.

    These equip the user's *interactive* claude CLI only — HIVE agents run
    with --strict-mcp-config and get servers from backend/mcp/catalog.py.
    """
    config_path = _claude_config_path()
    servers = _read_claude_config(config_path).get("mcpServers", {})
    return {
        "items": [
            {
                "key": key,
                "command": entry.get("command", ""),
                "args": entry.get("args", []),
            }
            for key, entry in servers.items()
            if isinstance(entry, dict)
        ],
        "config_path": str(config_path),
    }


# ── MCP install ──────────────────────────────────────────────────────────────

class MCPInstallRequest(BaseModel):
    id: str
    name: str
    install: dict
    permissions: list[str] = []


@router.post("/mcp/install")
async def mcp_install(req: MCPInstallRequest) -> dict:
    """Add the MCP server entry to ~/.claude.json mcpServers section.

    We don't shell out to `npm install` here — that's slow + can fail in
    ways unrelated to HIVE. The response includes the exact command the
    user should run in their terminal.
    """
    config_path = _claude_config_path()
    config = _read_claude_config(config_path)

    transport = (req.install or {}).get("transport", "npm")
    package = (req.install or {}).get("package", "")

    mcp_servers = config.setdefault("mcpServers", {})
    key = _safe_slug(req.name)
    if transport == "npm":
        mcp_servers[key] = {"command": "npx", "args": ["-y", package]}
        install_command = f"npm install -g {package}"
    elif transport == "pip":
        mcp_servers[key] = {"command": package, "args": []}
        install_command = f"pip install {package}"
    elif transport == "smithery":
        mcp_servers[key] = {"command": "smithery", "args": ["run", package]}
        install_command = f"smithery install {package}"
    else:
        mcp_servers[key] = {"command": package, "args": []}
        install_command = f"# install transport={transport} package={package} manually"

    _write_claude_config(config_path, config)

    return {
        "ok": True,
        "mcp_id": req.id,
        "config_path": str(config_path),
        "config_key": key,
        "command": install_command,
        "permissions": req.permissions,
    }


@router.delete("/mcp/{mcp_id:path}")
async def mcp_uninstall(mcp_id: str) -> dict:
    config_path = _claude_config_path()
    config = _read_claude_config(config_path)
    servers = config.get("mcpServers", {})
    key = _safe_slug(mcp_id.split("/", 1)[-1])
    removed = servers.pop(key, None)
    _write_claude_config(config_path, config)
    return {"ok": True, "removed": removed is not None, "config_key": key}


def _claude_config_path() -> Path:
    """Return the path Claude Code reads for its MCP config.

    Newer versions of `claude` use `~/.claude.json`; older versions used
    `~/.claude/config.json`. We prefer the one that already exists, falling
    back to `~/.claude.json` for new installs.
    """
    home = Path(os.path.expanduser("~"))
    candidates = [home / ".claude.json", home / ".claude" / "config.json"]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def _read_claude_config(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # The previous behaviour was to return {} here, which caused the
        # next _write_claude_config() to wipe every non-MCP key the user
        # had set (memory, tool configs, …). Save a timestamped backup
        # of the file as it stands and refuse the install instead — the
        # user can repair the original and retry.
        backup = path.with_suffix(path.suffix + f".corrupted.{int(time.time())}.bak")
        backup.write_text(text, encoding="utf-8")
        raise HTTPException(
            status_code=500,
            detail=(
                f"Refusing to overwrite {path}: contents are not valid JSON "
                f"({exc.msg} at line {exc.lineno}, col {exc.colno}). "
                f"A backup of the original has been saved to {backup}. "
                "Fix the original file and retry the install."
            ),
        ) from exc


def _write_claude_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
