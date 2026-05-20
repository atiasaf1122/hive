"""Slash command handlers — /start, /status, /sessions, /attach, /close, /help."""
from __future__ import annotations

import logging
from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from backend.persistence.events import get_session, list_sessions
from backend.telegram.config import load_config
from backend.telegram.session_router import attach_session, get_attached_session

logger = logging.getLogger(__name__)
router = Router()


def _is_allowed(chat_id: int) -> bool:
    return load_config().is_allowed(chat_id)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _is_allowed(message.chat.id):
        await message.answer(
            f"This chat ({message.chat.id}) is not allowed.\n"
            f"Run on the host: hive telegram allow {message.chat.id}"
        )
        return
    await message.answer(
        "HIVE bot connected.\n\n"
        "Commands:\n"
        "/sessions — list active sessions\n"
        "/attach <id> — focus on a session for chat\n"
        "/status — current session status\n"
        "/close — close the attached session\n"
        "/help — this message\n\n"
        "Free text is sent to the attached session's orchestrator."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await cmd_start(message)


@router.message(Command("sessions"))
async def cmd_sessions(message: Message) -> None:
    if not _is_allowed(message.chat.id):
        return
    sessions = await list_sessions(limit=10)
    if not sessions:
        await message.answer("No sessions yet.")
        return
    lines = ["*Recent sessions:*"]
    for s in sessions:
        status_emoji = _status_emoji(s["status"])
        lines.append(f"{status_emoji} `{s['id']}` — {s['name'][:50]} ({s['status']})")
    lines.append("\nUse /attach <id> to focus on one.")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("attach"))
async def cmd_attach(message: Message, command: CommandObject) -> None:
    if not _is_allowed(message.chat.id):
        return
    session_id = (command.args or "").strip()
    if not session_id:
        await message.answer("Usage: /attach <session-id>")
        return
    session = await get_session(session_id)
    if not session:
        await message.answer(f"Session `{session_id}` not found.", parse_mode="Markdown")
        return
    attach_session(message.chat.id, session_id)
    await message.answer(
        f"Attached to *{session['name'][:50]}* ({session_id}).\n"
        f"Status: {session['status']}.\n"
        f"Free text in this chat now goes to that orchestrator.",
        parse_mode="Markdown",
    )


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not _is_allowed(message.chat.id):
        return
    session_id = get_attached_session(message.chat.id)
    if not session_id:
        await message.answer("No session attached. Use /sessions then /attach <id>.")
        return
    session = await get_session(session_id)
    if not session:
        await message.answer(f"Attached session `{session_id}` no longer exists.", parse_mode="Markdown")
        return
    emoji = _status_emoji(session["status"])
    await message.answer(
        f"{emoji} *{session['name'][:50]}*\n"
        f"ID: `{session['id']}`\n"
        f"Status: {session['status']}\n"
        f"Last active: {session.get('last_active', '?')}",
        parse_mode="Markdown",
    )


@router.message(Command("close"))
async def cmd_close(message: Message) -> None:
    if not _is_allowed(message.chat.id):
        return
    session_id = get_attached_session(message.chat.id)
    if not session_id:
        await message.answer("No session attached.")
        return
    # Lazy import to avoid heavy api module on bot startup
    from backend.api.http import _pending_inputs, update_session_status
    future = _pending_inputs.get(session_id)
    if future and not future.done():
        future.set_result({"close": True})
        _pending_inputs.pop(session_id, None)
        await message.answer(f"Closing session `{session_id}`.", parse_mode="Markdown")
    else:
        await update_session_status(session_id, "closed")
        await message.answer(f"Session `{session_id}` marked closed.", parse_mode="Markdown")


def _status_emoji(status: str) -> str:
    return {
        "active": "🟢",
        "running": "🟢",
        "starting": "🟡",
        "waiting_approval": "🟡",
        "awaiting_user": "💬",
        "completed": "✅",
        "failed": "🔴",
        "closed": "⚪",
        "cancelled": "⚫",
    }.get(status, "•")
