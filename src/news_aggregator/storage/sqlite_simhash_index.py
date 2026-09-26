"""SQLite-реализация ISimhashIndex.

Хранит SimHash-отпечатки недавних сообщений и умеет находить все отпечатки
не старше заданного момента времени — этого достаточно, чтобы
SimHashDeduplicator сам считал расстояние Хэмминга до каждого кандидата.

Не растёт бесконечно: при каждом save() дополнительно можно вызвать
purge_older_than (это делает SimHashDeduplicator.remember), поэтому таблица
никогда не хранит больше данных, чем нужно для окна сравнения.

SimHash — 64-битное БЕЗЗНАКОВОЕ число, а SQLite INTEGER — знаковый 64-битный
тип. Поэтому при записи/чтении число переводится в/из знакового представления
(дополнительный код), чтобы не терять старший бит и не переполнять столбец.

Метки времени хранятся как ISO8601 в UTC (`datetime.now(UTC).isoformat()`) и
сравниваются как обычные строки — это работает корректно только если все
даты, приходящие в find_recent/purge_older_than, тоже в UTC (или хотя бы
единообразны). SimHashDeduplicator всегда передаёт datetime.now(UTC).

Имя таблицы настраиваемо (`table_name`): char- и word-режимы SimHash дают
принципиально разные, несравнимые между собой отпечатки для одного и того
же текста — если конфигурация использует оба режима (два
simhash_deduplicator с разным `unit`), у каждого должна быть своя таблица,
иначе find_recent() будет сравнивать word-отпечаток с char-отпечатками и
результат будет бессмысленным. main.py создаёт отдельный индекс на каждое
уникальное значение `index_name` из конфига (по умолчанию — "seen_simhashes",
как и раньше).
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from news_aggregator.core.interfaces import ISimhashIndex

# Имя таблицы подставляется в SQL напрямую (sqlite3 не параметризует
# идентификаторы, только значения), поэтому строго валидируем формат —
# только буквы/цифры/подчёркивание, не начинается с цифры.
_VALID_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DEFAULT_TABLE_NAME = "seen_simhashes"

_UINT64_MASK = (1 << 64) - 1
_INT64_SIGN_BIT = 1 << 63


def _to_signed_64(value: int) -> int:
    """Беззнаковое 64-битное число -> знаковое (для хранения в SQLite INTEGER)."""
    value &= _UINT64_MASK
    return value - (1 << 64) if value & _INT64_SIGN_BIT else value


def _from_signed_64(value: int) -> int:
    """Обратное преобразование: знаковое число из SQLite -> беззнаковый SimHash."""
    return value & _UINT64_MASK


class SqliteSimhashIndex(ISimhashIndex):
    """Хранит SimHash-отпечатки в настраиваемой SQLite-таблице.

    По умолчанию таблица называется `seen_simhashes` (как раньше) — для
    нескольких независимых индексов (например, char- и word-режим SimHash
    одновременно) передайте разные `table_name`.
    """

    def __init__(
        self, db_path: str = "data/state.db", table_name: str = _DEFAULT_TABLE_NAME
    ) -> None:
        if not _VALID_TABLE_NAME_RE.match(table_name):
            raise ValueError(
                f"Некорректное имя таблицы для SimHash-индекса: {table_name!r} "
                "(допустимы только буквы/цифры/подчёркивание, не начиная с цифры)"
            )
        self._db_path = db_path
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
                simhash INTEGER NOT NULL,
                source_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_{table_name}_created_at
                ON {table_name}(created_at);
            """
        )
        self._conn.commit()
        self._lock = asyncio.Lock()

    async def find_recent(self, since: datetime) -> list[int]:
        async with self._lock:
            return await asyncio.to_thread(self._find_recent_sync, since)

    def _find_recent_sync(self, since: datetime) -> list[int]:
        cursor = self._conn.execute(
            f"SELECT simhash FROM {self._table_name} WHERE created_at >= ?",  # noqa: S608
            (since.isoformat(),),
        )
        return [_from_signed_64(row[0]) for row in cursor.fetchall()]

    async def save(self, simhash: int, source_id: str, external_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, simhash, source_id, external_id)

    def _save_sync(self, simhash: int, source_id: str, external_id: str) -> None:
        created_at = datetime.now(UTC).isoformat()
        self._conn.execute(
            f"""
            INSERT INTO {self._table_name} (simhash, source_id, external_id, created_at)
            VALUES (?, ?, ?, ?)
            """,  # noqa: S608
            (_to_signed_64(simhash), source_id, external_id, created_at),
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
