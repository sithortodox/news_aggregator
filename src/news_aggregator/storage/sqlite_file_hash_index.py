"""SQLite-реализация IFileHashIndex: контент-хэши файлов недавних постов."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from news_aggregator.core.interfaces import IFileHashIndex
from news_aggregator.core.models import FileHashRecord

_VALID_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_TABLE_NAME = "seen_file_hashes"


class SqliteFileHashIndex(IFileHashIndex):
    def __init__(
        self,
        db_path: str = "data/state.db",
        table_name: str = _DEFAULT_TABLE_NAME,
    ) -> None:
        if not _VALID_TABLE_NAME_RE.match(table_name):
            raise ValueError(f"Некорректное имя таблицы для file-хэшей: {table_name!r}")
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
                file_hash TEXT NOT NULL,
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

    async def find_recent(self, since: datetime) -> list[FileHashRecord]:
        async with self._lock:
            return await asyncio.to_thread(self._find_recent_sync, since)

    def _find_recent_sync(self, since: datetime) -> list[FileHashRecord]:
        rows = self._conn.execute(
            f"SELECT file_hash, source_id, stems FROM {self._table_name} WHERE created_at >= ?",
            (since.isoformat(),),
        ).fetchall()
        return [
            FileHashRecord(
                file_hash=file_hash,
                source_id=source_id,
                stems=frozenset(stems.split()),
            )
            for file_hash, source_id, stems in rows
        ]

    async def save(self, record: FileHashRecord, external_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, record, external_id)

    def _save_sync(self, record: FileHashRecord, external_id: str) -> None:
        self._conn.execute(
            f"""
            INSERT INTO {self._table_name}
                (file_hash, source_id, external_id, stems, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record.file_hash,
                record.source_id,
                external_id,
                " ".join(sorted(record.stems)),
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()

    async def purge_older_than(self, cutoff: datetime) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._purge_sync, cutoff)

    def _purge_sync(self, cutoff: datetime) -> int:
        cursor = self._conn.execute(
            f"DELETE FROM {self._table_name} WHERE created_at < ?",
            (cutoff.isoformat(),),
        )
        self._conn.commit()
        return cursor.rowcount

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)
