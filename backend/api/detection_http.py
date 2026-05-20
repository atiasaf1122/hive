"""Detection endpoints surfaced to the desktop UI.

The Ollama server doesn't send CORS headers, so the WebView can't reach
``http://localhost:11434`` directly. We proxy through the backend so the
onboarding wizard + Settings can report what's installed without the
user opening a terminal.
"""
from __future__ import annotations

import platform

from fastapi import APIRouter

from backend.api import http as _http
from backend.detection import detect_backends

router = APIRouter(prefix="/api/detect")


@router.get("/host")
async def detect_host() -> dict:
    """Where is the backend running? Used by the UI to format hints.

    `wsl=True` lets the desktop shell warn that Windows-style paths
    will be auto-translated (e.g. C:\\Users\\… → /mnt/c/Users/…).
    """
    return {
        "system": platform.system(),
        "release": platform.release(),
        "wsl": _http._is_wsl(),
    }


@router.get("/backends")
async def detect_all() -> dict:
    """Single round-trip probe across every backend HIVE knows about."""
    status = await detect_backends()
    return {
        "claude_cli": status.claude_cli,
        "claude_cli_version": status.claude_cli_version,
        "claude_api": status.claude_api,
        "ollama_reachable": status.ollama,
        "ollama_models": status.ollama_models,
        "summary": status.summary(),
    }


@router.get("/ollama")
async def detect_ollama() -> dict:
    """Just the Ollama view — used by the onboarding wizard."""
    status = await detect_backends()
    return {
        "ollama_reachable": status.ollama,
        "models": status.ollama_models,
    }
