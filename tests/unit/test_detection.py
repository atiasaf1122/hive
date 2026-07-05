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
    # Patch the locator directly: shutil.which is no longer the only path
    # — _locate_claude_binary also probes ~/.local/bin/claude and friends,
    # so a CI / dev machine that actually has claude installed would have
    # this test pass when it should fail.
    with patch("backend.detection._locate_claude_binary", return_value=None), \
         patch("os.environ.get", return_value=None):
        status = await detect_backends()

    assert status.claude_cli is False
    assert status.ollama is False


# ── _locate_claude_binary: probe paths beyond $PATH ───────────────────────


def test_locate_claude_binary_prefers_path_first(monkeypatch, tmp_path):
    from backend import detection

    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\necho 2.1.0\n")
    binary.chmod(0o755)
    monkeypatch.setattr(detection.shutil, "which", lambda _: str(binary))

    assert detection._locate_claude_binary() == str(binary)


def test_locate_claude_binary_falls_back_to_common_paths(monkeypatch, tmp_path):
    """When PATH-based lookup misses (which on a stock WSL login shell where
    ~/.local/bin isn't exported), we should still find the installer's binary."""
    from backend import detection

    fake_local_bin = tmp_path / ".local" / "bin"
    fake_local_bin.mkdir(parents=True)
    binary = fake_local_bin / "claude"
    binary.write_text("#!/bin/sh\necho 2.1.0\n")
    binary.chmod(0o755)

    monkeypatch.setattr(detection.shutil, "which", lambda _: None)
    monkeypatch.setattr(detection.os.path, "expanduser",
                        lambda p: p.replace("~", str(tmp_path)))

    found = detection._locate_claude_binary()
    assert found == str(binary)


def test_locate_claude_binary_returns_none_when_truly_absent(monkeypatch, tmp_path):
    from backend import detection

    monkeypatch.setattr(detection.shutil, "which", lambda _: None)
    monkeypatch.setattr(detection.os.path, "expanduser",
                        lambda p: p.replace("~", str(tmp_path)))

    assert detection._locate_claude_binary() is None


def test_resolved_claude_path_defaults_to_bare_claude():
    """Until detection has cached a path, callers get the legacy 'claude'
    string so a PATH-based setup keeps working without any new wiring."""
    from backend import detection

    detection._set_resolved_claude_path(None)
    assert detection.resolved_claude_path() == "claude"


def test_resolved_claude_path_returns_cached_value():
    from backend import detection

    detection._set_resolved_claude_path("/opt/myclaude/bin/claude")
    try:
        assert detection.resolved_claude_path() == "/opt/myclaude/bin/claude"
    finally:
        detection._set_resolved_claude_path(None)
