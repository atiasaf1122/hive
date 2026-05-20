"""Persisted Telegram bot configuration.

Stored at ~/.hive/telegram.json, chmod 0600. Holds the bot token and the list
of chat IDs allowed to interact with the bot (prevents token-leak abuse).
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path

from backend.persistence.db import HIVE_DIR


def _default_path() -> Path:
    return HIVE_DIR / "telegram.json"


@dataclass
class TelegramConfig:
    token: str = ""
    allowed_chat_ids: list[int] = field(default_factory=list)
    notify_approvals: bool = True
    notify_session_end: bool = True
    quiet_hours: list[int] = field(default_factory=list)   # UTC hours, e.g. [22, 23, 0, 1, 2, 3, 4, 5, 6]

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def is_allowed(self, chat_id: int) -> bool:
        if not self.allowed_chat_ids:
            return False
        return chat_id in self.allowed_chat_ids


def load_config(path: Path | None = None) -> TelegramConfig:
    """Read the telegram config from disk. Returns an empty config if missing."""
    p = path or _default_path()
    if not p.exists():
        return TelegramConfig()
    data = json.loads(p.read_text())
    return TelegramConfig(
        token=data.get("token", ""),
        allowed_chat_ids=list(data.get("allowed_chat_ids", [])),
        notify_approvals=bool(data.get("notify_approvals", True)),
        notify_session_end=bool(data.get("notify_session_end", True)),
        quiet_hours=list(data.get("quiet_hours", [])),
    )


def save_config(config: TelegramConfig, path: Path | None = None) -> Path:
    """Write the config to disk with restricted permissions."""
    p = path or _default_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(config), indent=2))
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    return p


def add_allowed_chat(chat_id: int, path: Path | None = None) -> TelegramConfig:
    config = load_config(path)
    if chat_id not in config.allowed_chat_ids:
        config.allowed_chat_ids.append(chat_id)
        save_config(config, path)
    return config


def remove_allowed_chat(chat_id: int, path: Path | None = None) -> TelegramConfig:
    config = load_config(path)
    config.allowed_chat_ids = [c for c in config.allowed_chat_ids if c != chat_id]
    save_config(config, path)
    return config


def set_token(token: str, path: Path | None = None) -> TelegramConfig:
    config = load_config(path)
    config.token = token.strip()
    save_config(config, path)
    return config
