"""Tiny in-memory TTL cache for registry proxy responses.

A single-user desktop app doesn't justify Redis; we just hold a dict.
Entries are tagged with the moment they were written; reads check freshness.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    value: T
    written_at: float


class TtlCache:
    def __init__(self, ttl_seconds: float = 3600.0) -> None:
        self.ttl = ttl_seconds
        self._store: dict[str, CacheEntry[Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if (time.time() - entry.written_at) > self.ttl:
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = CacheEntry(value=value, written_at=time.time())

    def age_seconds(self, key: str) -> float | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        return time.time() - entry.written_at

    def clear(self) -> None:
        self._store.clear()
