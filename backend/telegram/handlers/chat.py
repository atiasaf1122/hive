"""Free-text chat handler — forwards user messages to the attached session's orchestrator.

This is the catch-all router (registered last). If the chat is attached to a
session, the message is delivered exactly like a `POST /sessions/{id}/message`
from the web UI — resolving any pending input future, or queuing otherwise.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from backend.telegram.config import load_config
from backend.telegram.session_router import get_attached_session

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text & ~F.text.startswith("/"))
async def handle_chat_message(message: Message) -> None:
    chat_id = message.chat.id
    if not load_config().is_allowed(chat_id):
        return

    session_id = get_attached_session(chat_id)
    if not session_id:
        await message.answer("No session attached. Use /sessions then /attach <id>.")
        return

    text = (message.text or "").strip()
    if not text:
        return

    await deliver_message_to_session(session_id, text)
    await message.answer("→ orchestrator", disable_notification=True)


async def deliver_message_to_session(session_id: str, text: str) -> bool:
    """Deliver a text message to a session, resolving pending future or queueing.

    Mirrors the logic of POST /api/sessions/{id}/message — kept here as a
    module-level function so it's directly callable from tests and other
    handlers (e.g., callback approvals that also want to message the session).
    Returns True if the message resolved a pending future, False if it was queued.
    """
    from backend.api import http as http_mod

    future = http_mod._pending_inputs.get(session_id)
    if future and not future.done():
        future.set_result({"text": text})
        http_mod._pending_inputs.pop(session_id, None)
        return True

    http_mod._get_queue(session_id).append(text)
    return False
