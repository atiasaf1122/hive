"""Phase 7 tests — Telegram bot config, handlers, routing, notifier formatting.

The bot itself isn't started for tests (no live Telegram). We test:
  - Config persistence + allowlist enforcement
  - Per-chat session attachment routing
  - Approval notifier message + keyboard formatting
  - Approval callback resolves the pending HTTP future
  - /attach, /sessions, /close commands
  - Free-text chat delivery to the orchestrator
"""
from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.telegram import session_router as srouter
from backend.telegram.config import (
    TelegramConfig,
    add_allowed_chat,
    load_config,
    remove_allowed_chat,
    save_config,
    set_token,
)
from backend.telegram.handlers.callbacks import (
    APPROVE_PREFIX,
    REJECT_PREFIX,
    build_approval_keyboard,
)
from backend.telegram.notifier import _format_approval


# ── config persistence ───────────────────────────────────────────────────────

def test_config_round_trip(tmp_path: Path) -> None:
    cfg_path = tmp_path / "telegram.json"
    config = TelegramConfig(token="abc:123", allowed_chat_ids=[111, 222])
    save_config(config, cfg_path)
    loaded = load_config(cfg_path)
    assert loaded.token == "abc:123"
    assert loaded.allowed_chat_ids == [111, 222]


def test_config_file_is_chmod_0600(tmp_path: Path) -> None:
    cfg_path = tmp_path / "telegram.json"
    save_config(TelegramConfig(token="t"), cfg_path)
    mode = stat.S_IMODE(os.stat(cfg_path).st_mode)
    assert mode == 0o600


def test_config_missing_returns_empty(tmp_path: Path) -> None:
    config = load_config(tmp_path / "nope.json")
    assert config.token == ""
    assert config.allowed_chat_ids == []
    assert config.enabled is False


def test_config_enabled_requires_token() -> None:
    assert TelegramConfig(token="").enabled is False
    assert TelegramConfig(token="abc").enabled is True


def test_is_allowed_empty_allowlist_blocks_all() -> None:
    assert TelegramConfig(token="t").is_allowed(123) is False


def test_is_allowed_with_match() -> None:
    cfg = TelegramConfig(token="t", allowed_chat_ids=[42])
    assert cfg.is_allowed(42) is True
    assert cfg.is_allowed(99) is False


def test_add_remove_chat(tmp_path: Path) -> None:
    cfg_path = tmp_path / "telegram.json"
    save_config(TelegramConfig(token="t"), cfg_path)
    add_allowed_chat(11, cfg_path)
    add_allowed_chat(22, cfg_path)
    add_allowed_chat(11, cfg_path)  # dupe ignored
    assert load_config(cfg_path).allowed_chat_ids == [11, 22]
    remove_allowed_chat(11, cfg_path)
    assert load_config(cfg_path).allowed_chat_ids == [22]


def test_set_token(tmp_path: Path) -> None:
    cfg_path = tmp_path / "telegram.json"
    set_token("123:abc", cfg_path)
    assert load_config(cfg_path).token == "123:abc"


# ── session router ───────────────────────────────────────────────────────────

def test_attach_and_get_session() -> None:
    srouter.clear()
    srouter.attach_session(chat_id=42, session_id="sess1")
    assert srouter.get_attached_session(42) == "sess1"
    assert 42 in srouter.get_subscribers("sess1")


def test_reattach_replaces_prior() -> None:
    srouter.clear()
    srouter.attach_session(42, "sess1")
    srouter.attach_session(42, "sess2")
    assert srouter.get_attached_session(42) == "sess2"
    assert 42 not in srouter.get_subscribers("sess1")
    assert 42 in srouter.get_subscribers("sess2")


def test_detach() -> None:
    srouter.clear()
    srouter.attach_session(42, "sess1")
    srouter.detach_chat(42)
    assert srouter.get_attached_session(42) is None
    assert srouter.get_subscribers("sess1") == set()


# ── approval notifier formatting ─────────────────────────────────────────────

def test_format_approval_includes_team_and_confidence() -> None:
    payload = {
        "team_composition": {
            "team": [
                {"role": "Builder", "model": "claude:sonnet", "count": 2, "passive": False},
                {"role": "Debugger", "model": "claude:sonnet", "count": 1, "passive": True},
            ],
            "confidence": 0.6,
            "rationale": "needs careful review",
        },
        "confidence": 0.6,
        "reason": "low_confidence",
    }
    text = _format_approval("sess-xyz", payload)
    assert "sess-xyz" in text
    assert "Builder" in text and "×2" in text
    assert "(passive)" in text  # for Debugger
    assert "60%" in text
    assert "low_confidence" in text
    assert "needs careful review" in text.lower()


def test_approval_keyboard_has_three_buttons() -> None:
    kb = build_approval_keyboard("abc")
    assert len(kb.inline_keyboard) == 1
    row = kb.inline_keyboard[0]
    assert len(row) == 3
    assert row[0].callback_data == f"{APPROVE_PREFIX}abc"
    assert row[1].callback_data == f"{REJECT_PREFIX}abc"


# ── callback handler resolves pending future ─────────────────────────────────

@pytest.mark.asyncio
async def test_callback_approve_resolves_pending_future() -> None:
    """Telegram approve callback now resolves by correlation_id, not session_id."""
    from backend.api import http as http_mod
    from backend.telegram.handlers.callbacks import on_approve

    http_mod._pending_approvals.clear()
    http_mod._session_to_corr_ids.clear()
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    http_mod._register_approval("corr-cb", "sess-cb", future)

    query = MagicMock()
    query.data = f"{APPROVE_PREFIX}corr-cb"
    query.message = MagicMock()
    query.message.chat = MagicMock()
    query.message.chat.id = 42
    query.message.text = "Original approval body"
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    with patch("backend.telegram.handlers.callbacks.load_config",
               return_value=TelegramConfig(token="t", allowed_chat_ids=[42])), \
         patch("backend.persistence.events.get_pending_approval",
               new_callable=AsyncMock, return_value={"session_id": "sess-cb"}), \
         patch("backend.persistence.events.resolve_pending_approval",
               new_callable=AsyncMock, return_value=True):
        await on_approve(query)

    assert future.done()
    assert future.result() == {"approved": True}
    query.answer.assert_awaited_once_with("✓ Approved")
    assert "corr-cb" not in http_mod._pending_approvals


@pytest.mark.asyncio
async def test_callback_reject_resolves_pending_future() -> None:
    from backend.api import http as http_mod
    from backend.telegram.handlers.callbacks import on_reject

    http_mod._pending_approvals.clear()
    http_mod._session_to_corr_ids.clear()
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    http_mod._register_approval("corr-rej", "sess-rej", future)

    query = MagicMock()
    query.data = f"{REJECT_PREFIX}corr-rej"
    query.message = MagicMock()
    query.message.chat = MagicMock()
    query.message.chat.id = 42
    query.message.text = "Original approval body"
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    with patch("backend.telegram.handlers.callbacks.load_config",
               return_value=TelegramConfig(token="t", allowed_chat_ids=[42])), \
         patch("backend.persistence.events.get_pending_approval",
               new_callable=AsyncMock, return_value={"session_id": "sess-rej"}), \
         patch("backend.persistence.events.resolve_pending_approval",
               new_callable=AsyncMock, return_value=True):
        await on_reject(query)

    assert future.result() == {"approved": False}


@pytest.mark.asyncio
async def test_callback_disallowed_chat_rejected() -> None:
    from backend.api import http as http_mod
    from backend.telegram.handlers.callbacks import on_approve

    http_mod._pending_approvals.clear()
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    http_mod._pending_approvals["sess-x"] = future

    query = MagicMock()
    query.data = f"{APPROVE_PREFIX}sess-x"
    query.message = MagicMock()
    query.message.chat = MagicMock()
    query.message.chat.id = 999  # NOT allowed
    query.answer = AsyncMock()
    query.message.edit_text = AsyncMock()

    with patch("backend.telegram.handlers.callbacks.load_config",
               return_value=TelegramConfig(token="t", allowed_chat_ids=[42])):
        await on_approve(query)

    assert not future.done()
    query.answer.assert_awaited_once_with("Not allowed.")


# ── chat handler routes to session ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_handler_delivers_message_to_pending_future() -> None:
    from backend.api import http as http_mod
    from backend.telegram.handlers.chat import deliver_message_to_session

    http_mod._pending_inputs.clear()
    http_mod._message_queues.clear()
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    http_mod._pending_inputs["sess-chat"] = future

    resolved = await deliver_message_to_session("sess-chat", "do more work")
    assert resolved is True
    assert future.result() == {"text": "do more work"}


@pytest.mark.asyncio
async def test_chat_handler_queues_message_when_busy() -> None:
    from backend.api import http as http_mod
    from backend.telegram.handlers.chat import deliver_message_to_session

    http_mod._pending_inputs.clear()
    http_mod._message_queues.clear()

    resolved = await deliver_message_to_session("sess-busy", "queued thought")
    assert resolved is False
    assert list(http_mod._get_queue("sess-busy")) == ["queued thought"]


# ── notifier respects allowlist + quiet hours ────────────────────────────────

@pytest.mark.asyncio
async def test_notify_approval_sends_to_subscribers_only(tmp_path: Path) -> None:
    from backend.telegram import notifier

    srouter.clear()
    srouter.attach_session(chat_id=111, session_id="sess-n")

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()

    cfg = TelegramConfig(token="t", allowed_chat_ids=[111, 222])

    with patch.object(notifier, "get_bot", return_value=fake_bot), \
         patch.object(notifier, "load_config", return_value=cfg):
        delivered = await notifier.notify_approval("sess-n", {
            "team_composition": {"team": [], "confidence": 0.9, "rationale": ""},
            "confidence": 0.9, "reason": "approval_mode",
        }, correlation_id="corr-n")

    assert delivered == 1
    fake_bot.send_message.assert_awaited_once()
    args, kwargs = fake_bot.send_message.await_args
    assert args[0] == 111  # only subscribed chat


@pytest.mark.asyncio
async def test_notify_approval_falls_back_to_allowlist_when_no_subscribers() -> None:
    from backend.telegram import notifier

    srouter.clear()  # no subscribers
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    cfg = TelegramConfig(token="t", allowed_chat_ids=[111, 222])

    with patch.object(notifier, "get_bot", return_value=fake_bot), \
         patch.object(notifier, "load_config", return_value=cfg):
        delivered = await notifier.notify_approval("sess-broadcast", {
            "team_composition": {"team": [], "confidence": 0.9, "rationale": ""},
            "confidence": 0.9, "reason": "approval_mode",
        }, correlation_id="corr-bc")

    assert delivered == 2


@pytest.mark.asyncio
async def test_notify_approval_skipped_when_bot_off() -> None:
    from backend.telegram import notifier
    with patch.object(notifier, "get_bot", return_value=None):
        delivered = await notifier.notify_approval("sess-x", {})
    assert delivered == 0


@pytest.mark.asyncio
async def test_notify_approval_skipped_when_disabled() -> None:
    from backend.telegram import notifier
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    cfg = TelegramConfig(token="t", allowed_chat_ids=[111], notify_approvals=False)
    with patch.object(notifier, "get_bot", return_value=fake_bot), \
         patch.object(notifier, "load_config", return_value=cfg):
        delivered = await notifier.notify_approval("sess-x", {})
    assert delivered == 0
    fake_bot.send_message.assert_not_awaited()
