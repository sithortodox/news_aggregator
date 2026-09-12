"""SQLite-реализация IStorage.

Это единственный модуль в проекте, где используется sqlite3. Ядро и
пайплайн работают только через интерфейс IStorage и ничего не знают
о том, что состояние хранится именно в SQLite.

sqlite3 в стандартной библиотеке синхронный, поэтому операции выполняются
в отдельном потоке через asyncio.to_thread, чтобы не блокировать event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from news_aggregator.core.interfaces import IStorage

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_cursors (
    source_id TEXT PRIMARY KEY,
    last_external_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_hashes (
    text_hash TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS seen_links (
    link TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class SqliteStorage(IStorage):
    """Хранит курсоры источников и историю хэшей/ссылок в SQLite."""

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

    async def get_last_external_id(self, source_id: str) -> str | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_last_external_id_sync, source_id)

    def _get_last_external_id_sync(self, source_id: str) -> str | None:
        cursor = self._conn.execute(
            "SELECT last_external_id FROM source_cursors WHERE source_id = ?",
            (source_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    async def set_last_external_id(self, source_id: str, external_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._set_last_external_id_sync, source_id, external_id
            )

    def _set_last_external_id_sync(self, source_id: str, external_id: str) -> None:
        self._conn.execute(
            """
            INSERT INTO source_cursors (source_id, last_external_id)
            VALUES (?, ?)
            ON CONFLICT(source_id) DO UPDATE SET last_external_id = excluded.last_external_id
            """,
            (source_id, external_id),
        )
        self._conn.commit()

    async def has_hash(self, text_hash: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._has_hash_sync, text_hash)

    def _has_hash_sync(self, text_hash: str) -> bool:
        cursor = self._conn.execute(
            "SELECT 1 FROM seen_hashes WHERE text_hash = ?", (text_hash,)
        )
        return cursor.fetchone() is not None

    async def save_hash(self, text_hash: str, source_id: str, external_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._save_hash_sync, text_hash, source_id, external_id
            )

    def _save_hash_sync(self, text_hash: str, source_id: str, external_id: str) -> None:
        self._conn.execute(
            """
            INSERT INTO seen_hashes (text_hash, source_id, external_id)
            VALUES (?, ?, ?)
            ON CONFLICT(text_hash) DO NOTHING
            """,
            (text_hash, source_id, external_id),
        )
        self._conn.commit()

    async def has_link(self, link: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._has_link_sync, link)

    def _has_link_sync(self, link: str) -> bool:
        cursor = self._conn.execute("SELECT 1 FROM seen_links WHERE link = ?", (link,))
        return cursor.fetchone() is not None

    async def save_link(self, link: str, source_id: str, external_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_link_sync, link, source_id, external_id)

    def _save_link_sync(self, link: str, source_id: str, external_id: str) -> None:
        self._conn.execute(
            """
            INSERT INTO seen_links (link, source_id, external_id)
            VALUES (?, ?, ?)
            ON CONFLICT(link) DO NOTHING
            """,
            (link, source_id, external_id),
        )
        self._conn.commit()

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)
