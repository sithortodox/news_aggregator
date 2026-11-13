"""SQLite-реализация IEmbeddingIndex.

Вектор хранится как BLOB из float32 (384 числа — 1,5 КБ на пост). Записей
столько, сколько постов вышло за окно сравнения: при каждом remember()
дедупликатор удаляет устаревшие.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import struct
from datetime import UTC, datetime
from pathlib import Path

from news_aggregator.core.interfaces import IEmbeddingIndex
from news_aggregator.core.models import EmbeddingRecord

_VALID_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_TABLE_NAME = "seen_embeddings"


def _pack(vector: tuple[float, ...]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(blob) // 4}f", blob)


class SqliteEmbeddingIndex(IEmbeddingIndex):
    def __init__(
        self, db_path: str = "data/state.db", table_name: str = _DEFAULT_TABLE_NAME
    ) -> None:
        if not _VALID_TABLE_NAME_RE.match(table_name):
            raise ValueError(
                f"Некорректное имя таблицы для векторов: {table_name!r} "
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
                vector BLOB NOT NULL,
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

    async def find_recent(self, since: datetime) -> list[EmbeddingRecord]:
        async with self._lock:
            return await asyncio.to_thread(self._find_recent_sync, since)

    def _find_recent_sync(self, since: datetime) -> list[EmbeddingRecord]:
        rows = self._conn.execute(
            f"SELECT vector, source_id, stems FROM {self._table_name} "  # noqa: S608
            "WHERE created_at >= ?",
            (since.isoformat(),),
        ).fetchall()
        return [
            EmbeddingRecord(
                vector=_unpack(vector), source_id=source_id, stems=frozenset(stems.split())
            )
            for vector, source_id, stems in rows
        ]

    async def save(self, record: EmbeddingRecord, external_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, record, external_id)

    def _save_sync(self, record: EmbeddingRecord, external_id: str) -> None:
        self._conn.execute(
            f"""
            INSERT INTO {self._table_name}
                (vector, source_id, external_id, stems, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,  # noqa: S608
            (
                _pack(record.vector),
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
            f"DELETE FROM {self._table_name} WHERE created_at < ?",  # noqa: S608
            (cutoff.isoformat(),),
        )
        self._conn.commit()
        return cursor.rowcount

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)
