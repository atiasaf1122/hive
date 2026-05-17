"""Tests for backend detection logic."""
from __future__ import annotations

import pytest
import respx
import httpx
from unittest.mock import patch, MagicMock, AsyncMock

from backend.detection import detect_backends


@pytest.mark.asyncio
@respx.mock
async def test_ollama_detected_when_running():
    respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "llama3.1"}, {"name": "qwen2.5"}]})
    )
    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"claude 2.1.0\n", b""))
        mock_exec.return_value = mock_proc

        status = await detect_backends()

    assert status.ollama is True
    assert "llama3.1" in status.ollama_models
    assert "qwen2.5" in status.ollama_models


@pytest.mark.asyncio
@respx.mock
async def test_ollama_not_detected_when_down():
    respx.get("http://localhost:11434/api/tags").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with patch("shutil.which", return_value=None):
        status = await detect_backends()

    assert status.ollama is False
    assert status.ollama_models == []


@pytest.mark.asyncio
@respx.mock
async def test_claude_cli_detected():
    respx.get("http://localhost:11434/api/tags").mock(
        side_effect=httpx.ConnectError("down")
    )
    with patch("shutil.which", return_value="/usr/bin/claude"), \
         patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"2.1.140 (Claude Code)\n", b""))
        mock_exec.return_value = mock_proc

        status = await detect_backends()

    assert status.claude_cli is True
    assert "2.1.140" in status.claude_cli_version


@pytest.mark.asyncio
@respx.mock
async def test_no_backends_when_nothing_available():
    respx.get("http://localhost:11434/api/tags").mock(
        side_effect=httpx.ConnectError("down")
    )
    with patch("shutil.which", return_value=None), \
         patch("os.environ.get", return_value=None):
        status = await detect_backends()

    assert status.claude_cli is False
    assert status.ollama is False
