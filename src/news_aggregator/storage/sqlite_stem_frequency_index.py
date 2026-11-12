"""SQLite-реализация IStemFrequencyIndex.

Одна таблица: основа слова и число сообщений, в которых она встречалась.
Общее число учтённых сообщений лежит в строке с пустой основой (настоящая
основа пустой не бывает). Таблица растёт только за счёт новых слов: даже
при тысяче постов в день это десятки тысяч строк в год.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from pathlib import Path

from news_aggregator.core.interfaces import IStemFrequencyIndex

_VALID_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_TABLE_NAME = "stem_frequencies"
_TOTAL_KEY = ""


class SqliteStemFrequencyIndex(IStemFrequencyIndex):
    def __init__(
        self, db_path: str = "data/state.db", table_name: str = _DEFAULT_TABLE_NAME
    ) -> None:
        if not _VALID_TABLE_NAME_RE.match(table_name):
            raise ValueError(
                f"Некорректное имя таблицы для частот слов: {table_name!r} "
                "(допустимы только буквы/цифры/подчёркивание, не начиная с цифры)"
            )
        self._table_name = table_name
        path = Path(db_path)
        if path.parent and str(path.parent) not in (".", ""):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                stem TEXT PRIMARY KEY,
                documents INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()
        self._lock = asyncio.Lock()

    async def load(self) -> tuple[int, dict[str, int]]:
        async with self._lock:
            return await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> tuple[int, dict[str, int]]:
        rows = self._conn.execute(
            f"SELECT stem, documents FROM {self._table_name}"  # noqa: S608
        ).fetchall()
        counts = {stem: int(documents) for stem, documents in rows}
        total = counts.pop(_TOTAL_KEY, 0)
        return total, counts

    async def add(self, stems: frozenset[str]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._add_sync, stems)

    def _add_sync(self, stems: frozenset[str]) -> None:
        keys = [_TOTAL_KEY, *sorted(stem for stem in stems if stem)]
        self._conn.executemany(
            f"""
            INSERT INTO {self._table_name} (stem, documents) VALUES (?, 1)
            ON CONFLICT(stem) DO UPDATE SET documents = documents + 1
            """,  # noqa: S608
            [(key,) for key in keys],
        )
        self._conn.commit()

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)
