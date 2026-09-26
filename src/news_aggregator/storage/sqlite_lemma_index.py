"""SQLite-реализация ILemmaSetIndex.

Хранит множество лемм (см. core/lemmatize.py) каждого недавнего сообщения
как одну строку (леммы через пробел — они уже не содержат пробелов сами по
себе) и умеет находить все такие множества не старше заданного момента
времени, чтобы ParaphraseDeduplicator сам считал коэффициент Жаккара до
каждого кандидата.

Не растёт бесконечно: как и SqliteSimhashIndex, при каждом save()
дополнительно можно вызвать purge_older_than (это делает
ParaphraseDeduplicator.remember).

Имя таблицы настраиваемо (`table_name`) по тем же соображениям, что и у
SqliteSimhashIndex — на случай нескольких независимых индексов в одной базе.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from news_aggregator.core.interfaces import ILemmaSetIndex

_VALID_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_TABLE_NAME = "seen_lemma_sets"


class SqliteLemmaIndex(ILemmaSetIndex):
    """Хранит множества лемм в настраиваемой SQLite-таблице.

    По умолчанию таблица называется `seen_lemma_sets`.
    """

    def __init__(
        self, db_path: str = "data/state.db", table_name: str = _DEFAULT_TABLE_NAME
    ) -> None:
        if not _VALID_TABLE_NAME_RE.match(table_name):
            raise ValueError(
                f"Некорректное имя таблицы для индекса лемм: {table_name!r} "
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
                lemmas TEXT NOT NULL,
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

    async def find_recent(self, since: datetime) -> list[frozenset[str]]:
        async with self._lock:
            return await asyncio.to_thread(self._find_recent_sync, since)

    def _find_recent_sync(self, since: datetime) -> list[frozenset[str]]:
        cursor = self._conn.execute(
            f"SELECT lemmas FROM {self._table_name} WHERE created_at >= ?",  # noqa: S608
            (since.isoformat(),),
        )
        return [frozenset(row[0].split()) for row in cursor.fetchall() if row[0]]

    async def save(self, lemmas: frozenset[str], source_id: str, external_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, lemmas, source_id, external_id)

    def _save_sync(self, lemmas: frozenset[str], source_id: str, external_id: str) -> None:
        created_at = datetime.now(UTC).isoformat()
        self._conn.execute(
            f"""
            INSERT INTO {self._table_name} (lemmas, source_id, external_id, created_at)
            VALUES (?, ?, ?, ?)
            """,  # noqa: S608
            (" ".join(sorted(lemmas)), source_id, external_id, created_at),
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
