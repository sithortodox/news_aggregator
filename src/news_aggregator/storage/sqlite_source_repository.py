"""SQLite-реализация ISourceRepository.

Хранит список источников (каналов/групп) отдельно от IStorage-состояния
(курсоров и истории хэшей): это то, что можно менять во время работы
приложения — например, командами Telegram-бота (/add, /remove, /list) —
без перезапуска процесса и без потери накопленного состояния пайплайна.

Обычно указывает на тот же файл БД, что и SqliteStorage (в SQLite это
нормально — просто ещё одна таблица), но технически это независимый
компонент и может использовать отдельный файл.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from news_aggregator.core.interfaces import ISourceRepository
from news_aggregator.core.models import Source, SourceKind

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    identifier TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class SqliteSourceRepository(ISourceRepository):
    """Хранит источники в SQLite-таблице `sources`."""

    def __init__(self, db_path: str = "data/state.db") -> None:
        self._db_path = db_path
        path = Path(db_path)
        if path.parent and str(path.parent) not in (".", ""):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = asyncio.Lock()

    async def list_sources(self) -> list[Source]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sources_sync)

    def _list_sources_sync(self) -> list[Source]:
        cursor = self._conn.execute(
            "SELECT id, kind, identifier, display_name, enabled FROM sources ORDER BY rowid"
        )
        return [self._row_to_source(row) for row in cursor.fetchall()]

    async def get_source(self, source_id: str) -> Source | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_source_sync, source_id)

    def _get_source_sync(self, source_id: str) -> Source | None:
        cursor = self._conn.execute(
            "SELECT id, kind, identifier, display_name, enabled FROM sources WHERE id = ?",
            (source_id,),
        )
        row = cursor.fetchone()
        return self._row_to_source(row) if row else None

    async def add_source(self, source: Source) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._add_source_sync, source)

    def _add_source_sync(self, source: Source) -> bool:
        existing = self._conn.execute(
            "SELECT 1 FROM sources WHERE id = ? OR identifier = ?",
            (source.id, source.identifier),
        ).fetchone()
        if existing:
            return False
        self._conn.execute(
            """
            INSERT INTO sources (id, kind, identifier, display_name, enabled)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                source.id,
                source.kind.value,
                source.identifier,
                source.display_name,
                1 if source.enabled else 0,
            ),
        )
        self._conn.commit()
        return True

    async def remove_source(self, source_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._remove_source_sync, source_id)

    def _remove_source_sync(self, source_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    async def set_enabled(self, source_id: str, enabled: bool) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._set_enabled_sync, source_id, enabled)

    def _set_enabled_sync(self, source_id: str, enabled: bool) -> bool:
        cursor = self._conn.execute(
            "UPDATE sources SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, source_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)

    @staticmethod
    def _row_to_source(row: tuple[str, str, str, str, int]) -> Source:
        source_id, kind_raw, identifier, display_name, enabled = row
        return Source(
            id=source_id,
            kind=SourceKind(kind_raw),
            identifier=identifier,
            display_name=display_name,
            enabled=bool(enabled),
        )
