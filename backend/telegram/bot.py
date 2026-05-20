"""HIVE Telegram bot — lifecycle + dispatcher wiring.

The bot is started lazily by the FastAPI lifespan if a valid token is
configured. It runs in the same event loop (no separate process).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher

from backend.telegram.config import TelegramConfig, load_config

logger = logging.getLogger(__name__)

_bot: Optional[Bot] = None
_dispatcher: Optional[Dispatcher] = None
_polling_task: Optional[asyncio.Task] = None


def get_bot() -> Optional[Bot]:
    """Return the running Bot instance, or None if not started."""
    return _bot


def get_dispatcher() -> Optional[Dispatcher]:
    return _dispatcher


async def start_bot(config: TelegramConfig | None = None) -> bool:
    """Start the bot. Returns True if started, False if no token / disabled."""
    global _bot, _dispatcher, _polling_task

    if _polling_task and not _polling_task.done():
        logger.info("Telegram bot already running")
        return True

    cfg = config or load_config()
    if not cfg.enabled:
        logger.info("Telegram bot not started: no token configured")
        return False

    _bot = Bot(token=cfg.token)
    _dispatcher = Dispatcher()
    _register_handlers(_dispatcher, cfg)

    _polling_task = asyncio.create_task(_dispatcher.start_polling(_bot), name="telegram-bot")
    logger.info("Telegram bot started (polling)")
    return True


async def stop_bot() -> None:
    """Stop polling and close the bot session."""
    global _bot, _dispatcher, _polling_task

    if _dispatcher:
        try:
            await _dispatcher.stop_polling()
        except Exception as exc:
            logger.warning("Error stopping dispatcher: %s", exc)

    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        try:
            await _polling_task
        except (asyncio.CancelledError, Exception):
            pass

    if _bot:
        try:
            await _bot.session.close()
        except Exception as exc:
            logger.warning("Error closing bot session: %s", exc)

    _bot = None
    _dispatcher = None
    _polling_task = None


def _register_handlers(dispatcher: Dispatcher, config: TelegramConfig) -> None:
    """Attach all command, chat, and callback routers to the dispatcher."""
    # Late import so tests that don't need handlers don't pay the cost.
    from backend.telegram.handlers import callbacks, chat, commands

    dispatcher.include_router(commands.router)
    dispatcher.include_router(callbacks.router)
    dispatcher.include_router(chat.router)  # last — chat is the catch-all
