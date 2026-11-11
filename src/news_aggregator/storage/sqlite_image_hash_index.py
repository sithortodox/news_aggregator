"""SQLite-реализация IImageHashIndex.

Хэш хранится шестнадцатеричной строкой: 256 бит не помещаются в INTEGER
SQLite. Основы лемм текста — строкой через пробел, как в SqliteLemmaIndex.
Сравнение по расстоянию Хэмминга делает ImageDeduplicator: записей за сутки
сотни, полный перебор дешевле любого индекса.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from news_aggregator.core.interfaces import IImageHashIndex
from news_aggregator.core.models import ImageHashRecord

_VALID_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_TABLE_NAME = "seen_image_hashes"


class SqliteImageHashIndex(IImageHashIndex):
    """Хранит отпечатки картинок в настраиваемой SQLite-таблице."""

    def __init__(
        self, db_path: str = "data/state.db", table_name: str = _DEFAULT_TABLE_NAME
    ) -> None:
        if not _VALID_TABLE_NAME_RE.match(table_name):
            raise ValueError(
                f"Некорректное имя таблицы для индекса картинок: {table_name!r} "
                "(допустимы только буквы/цифры/подчёркивание, не начиная с цифры)"
            )
        self._table_name = table_name
        path = Path(db_path)
        if path.parent and str(path.parent) not in (".", ""):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_hash TEXT NOT NULL,
                source_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                stems TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_{table_name}_created_at
                ON {table_name}(created_at);
            """
        )
        self._conn.commit()
        self._lock = asyncio.Lock()

    async def find_recent(self, since: datetime) -> list[ImageHashRecord]:
        async with self._lock:
            return await asyncio.to_thread(self._find_recent_sync, since)

    def _find_recent_sync(self, since: datetime) -> list[ImageHashRecord]:
        cursor = self._conn.execute(
            f"SELECT image_hash, source_id, stems FROM {self._table_name} "  # noqa: S608
            "WHERE created_at >= ?",
            (since.isoformat(),),
        )
        return [
            ImageHashRecord(
                image_hash=int(image_hash, 16),
                source_id=source_id,
                stems=frozenset(stems.split()),
            )
            for image_hash, source_id, stems in cursor.fetchall()
        ]

    async def save(self, record: ImageHashRecord, external_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, record, external_id)

    def _save_sync(self, record: ImageHashRecord, external_id: str) -> None:
        self._conn.execute(
            f"""
            INSERT INTO {self._table_name}
                (image_hash, source_id, external_id, stems, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,  # noqa: S608
            (
                format(record.image_hash, "x"),
                record.source_id,
                external_id,
                " ".join(sorted(record.stems)),
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()

    async def purge_older_than(self, cutoff: datetime) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._purge_older_than_sync, cutoff)

    def _purge_older_than_sync(self, cutoff: datetime) -> int:
        cursor = self._conn.execute(
            f"DELETE FROM {self._table_name} WHERE created_at < ?",  # noqa: S608
            (cutoff.isoformat(),),
        )
        self._conn.commit()
        return cursor.rowcount

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)
