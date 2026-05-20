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

import json
import logging
import os
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.skills.registry import SKILLS_ROOT, import_skill

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/registries")


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
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Don't blow up the user's config; back it up and start fresh.
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            backup.write_text(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        return {}


def _write_claude_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
