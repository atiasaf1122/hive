"""Per-chat session attachment map.

Each Telegram chat can be "attached" to one HIVE session. Free-text messages
in that chat get routed to the attached session's orchestrator. Mapping is
in-memory and lost on restart — that's fine, the user can /attach again.
"""
from __future__ import annotations

from typing import Optional

_attached: dict[int, str] = {}    # chat_id -> session_id
_reverse: dict[str, set[int]] = {}  # session_id -> chat_ids subscribed


def attach_session(chat_id: int, session_id: str) -> None:
    """Bind a chat to a session. Replaces any prior attachment."""
    detach_chat(chat_id)
    _attached[chat_id] = session_id
    _reverse.setdefault(session_id, set()).add(chat_id)


def detach_chat(chat_id: int) -> None:
    prior = _attached.pop(chat_id, None)
    if prior and prior in _reverse:
        _reverse[prior].discard(chat_id)
        if not _reverse[prior]:
            _reverse.pop(prior, None)


def get_attached_session(chat_id: int) -> Optional[str]:
    return _attached.get(chat_id)


def get_subscribers(session_id: str) -> set[int]:
    """All chat_ids attached to a session — used for broadcast notifications."""
    return set(_reverse.get(session_id, set()))


def clear() -> None:
    """Test helper — wipe all mappings."""
    _attached.clear()
    _reverse.clear()
