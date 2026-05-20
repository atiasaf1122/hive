"""Push notifications from HIVE → Telegram (approvals, session events).

All sends are best-effort: if the bot isn't running or the chat is offline,
calls return False instead of raising. The HTTP/WS layer should never block
on Telegram delivery.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from backend.telegram.bot import get_bot
from backend.telegram.config import load_config
from backend.telegram.handlers.callbacks import build_approval_keyboard
from backend.telegram.session_router import get_subscribers

logger = logging.getLogger(__name__)


async def notify_approval(session_id: str, payload: dict) -> int:
    """Push an approval-request card to every chat attached to this session.

    Returns the number of chats successfully notified.
    """
    bot = get_bot()
    if bot is None:
        return 0

    cfg = load_config()
    if not cfg.notify_approvals or _in_quiet_hours(cfg):
        return 0

    chats = _resolve_target_chats(session_id, cfg)
    if not chats:
        return 0

    text = _format_approval(session_id, payload)
    keyboard = build_approval_keyboard(session_id)

    delivered = 0
    for chat_id in chats:
        try:
            await bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=keyboard)
            delivered += 1
        except Exception as exc:
            logger.warning("Failed to notify chat %s: %s", chat_id, exc)
    return delivered


async def notify_session_end(session_id: str, summary: str, cost_usd: float) -> int:
    """Notify when a session closes/completes."""
    bot = get_bot()
    if bot is None:
        return 0

    cfg = load_config()
    if not cfg.notify_session_end or _in_quiet_hours(cfg):
        return 0

    chats = _resolve_target_chats(session_id, cfg)
    if not chats:
        return 0

    body = summary.strip()[:1500] if summary else "(no output)"
    text = (
        f"*Session closed:* `{session_id}`\n"
        f"Cost: ${cost_usd:.4f}\n\n"
        f"{body}"
    )

    delivered = 0
    for chat_id in chats:
        try:
            await bot.send_message(chat_id, text, parse_mode="Markdown")
            delivered += 1
        except Exception as exc:
            logger.warning("Failed to notify chat %s: %s", chat_id, exc)
    return delivered


def _resolve_target_chats(session_id: str, cfg) -> list[int]:
    """Subscribers first, otherwise broadcast to every allowed chat."""
    subs = get_subscribers(session_id)
    if subs:
        return list(subs)
    return list(cfg.allowed_chat_ids)


def _format_approval(session_id: str, payload: dict) -> str:
    comp = payload.get("team_composition", {}) or {}
    confidence = payload.get("confidence", 1.0)
    reason = payload.get("reason", "")
    rationale = comp.get("rationale", "")

    lines = [
        f"*Approval needed — `{session_id}`*",
        f"Reason: {reason}",
        f"Confidence: {confidence:.0%}",
        "",
        "*Proposed team:*",
    ]
    for m in comp.get("team", []):
        tag = " (passive)" if m.get("passive") else ""
        lines.append(f"• {m.get('role','?')} ×{m.get('count', 1)} [{m.get('model','?')}]{tag}")
    if rationale:
        lines.append("")
        lines.append(f"_{rationale}_")
    return "\n".join(lines)


def _in_quiet_hours(cfg) -> bool:
    if not cfg.quiet_hours:
        return False
    now_hour = dt.datetime.utcnow().hour
    return now_hour in cfg.quiet_hours
