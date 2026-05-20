"""Inline callback handlers — approve/reject team compositions from Telegram."""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from backend.telegram.config import load_config

logger = logging.getLogger(__name__)
router = Router()

# Callback data conventions:
#   "approve:<session_id>"
#   "reject:<session_id>"
#   "diff:<session_id>"
APPROVE_PREFIX = "approve:"
REJECT_PREFIX = "reject:"
DIFF_PREFIX = "diff:"


def build_approval_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Inline-button row attached to approval notifications."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✓ Approve", callback_data=f"{APPROVE_PREFIX}{session_id}"),
        InlineKeyboardButton(text="✗ Reject",  callback_data=f"{REJECT_PREFIX}{session_id}"),
        InlineKeyboardButton(text="👁 Details", callback_data=f"{DIFF_PREFIX}{session_id}"),
    ]])


@router.callback_query(lambda c: c.data and c.data.startswith(APPROVE_PREFIX))
async def on_approve(query: CallbackQuery) -> None:
    if not _allowed(query):
        await query.answer("Not allowed.")
        return
    session_id = (query.data or "")[len(APPROVE_PREFIX):]
    resolved = await _resolve_approval(session_id, approved=True)
    msg = "✓ Approved" if resolved else "No pending approval (already handled?)"
    await query.answer(msg)
    if query.message and resolved:
        await query.message.edit_text(f"{query.message.text}\n\n*✓ Approved via Telegram*", parse_mode="Markdown")


@router.callback_query(lambda c: c.data and c.data.startswith(REJECT_PREFIX))
async def on_reject(query: CallbackQuery) -> None:
    if not _allowed(query):
        await query.answer("Not allowed.")
        return
    session_id = (query.data or "")[len(REJECT_PREFIX):]
    resolved = await _resolve_approval(session_id, approved=False)
    msg = "✗ Rejected" if resolved else "No pending approval"
    await query.answer(msg)
    if query.message and resolved:
        await query.message.edit_text(f"{query.message.text}\n\n*✗ Rejected via Telegram*", parse_mode="Markdown")


@router.callback_query(lambda c: c.data and c.data.startswith(DIFF_PREFIX))
async def on_diff(query: CallbackQuery) -> None:
    if not _allowed(query):
        await query.answer("Not allowed.")
        return
    await query.answer("Details: use the web UI for the full diff view.", show_alert=True)


async def _resolve_approval(session_id: str, approved: bool) -> bool:
    """Resolve the pending approval future. Returns True if a future was waiting."""
    from backend.api import http as http_mod

    future = http_mod._pending_approvals.get(session_id)
    if not future or future.done():
        return False
    future.set_result({"approved": approved})
    http_mod._pending_approvals.pop(session_id, None)
    return True


def _allowed(query: CallbackQuery) -> bool:
    if not query.message or not query.message.chat:
        return False
    return load_config().is_allowed(query.message.chat.id)
