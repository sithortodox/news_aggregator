"""SQLite-реализация IRunStatsStore: по строке на каждый прогон пайплайна.

Нужна для команды /stats управляющего бота и как основа для алертинга:
PipelineStats раньше только логировался и после прогона терялся.

Рост ограничен: при каждой записи удаляются прогоны старше retention_days
(по умолчанию 7 суток). При опросе раз в минуту это ~10 тыс. строк по
~50 байт, то есть меньше мегабайта.

Метки времени хранятся как ISO8601 в UTC и сравниваются как строки — как и
в SqliteSimhashIndex, это корректно, пока все даты в UTC.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from news_aggregator.core.interfaces import IRunStatsStore
from news_aggregator.core.models import PipelineStats, StatsSummary

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    sources_total INTEGER NOT NULL,
    sources_failed INTEGER NOT NULL,
    messages_fetched INTEGER NOT NULL,
    messages_filtered_out INTEGER NOT NULL,
    messages_duplicate INTEGER NOT NULL,
    messages_published INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_stats_created_at ON run_stats(created_at);
"""

DEFAULT_RETENTION_DAYS = 7.0


class SqliteRunStatsStore(IRunStatsStore):
    """Хранит итоги прогонов в SQLite-таблице `run_stats`."""

    def __init__(
        self, db_path: str = "data/state.db", retention_days: float = DEFAULT_RETENTION_DAYS
    ) -> None:
        if retention_days <= 0:
            raise ValueError("retention_days должен быть положительным")
        self._retention = timedelta(days=retention_days)
        path = Path(db_path)
        if path.parent and str(path.parent) not in (".", ""):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = asyncio.Lock()

    async def record(self, stats: PipelineStats, at: datetime | None = None) -> None:
        moment = at or datetime.now(UTC)
        async with self._lock:
            await asyncio.to_thread(self._record_sync, stats, moment)

    def _record_sync(self, stats: PipelineStats, moment: datetime) -> None:
        self._conn.execute(
            """
            INSERT INTO run_stats (
                created_at, sources_total, sources_failed, messages_fetched,
                messages_filtered_out, messages_duplicate, messages_published
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                moment.isoformat(),
                stats.sources_total,
                stats.sources_failed,
                stats.messages_fetched,
                stats.messages_filtered_out,
                stats.messages_duplicate,
                stats.messages_published,
            ),
        )
        self._conn.execute(
            "DELETE FROM run_stats WHERE created_at < ?",
            ((moment - self._retention).isoformat(),),
        )
        self._conn.commit()

    async def summarize(self, since: datetime) -> StatsSummary:
        async with self._lock:
            return await asyncio.to_thread(self._summarize_sync, since)

    def _summarize_sync(self, since: datetime) -> StatsSummary:
        row = self._conn.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(messages_fetched), 0),
                COALESCE(SUM(messages_filtered_out), 0),
                COALESCE(SUM(messages_duplicate), 0),
                COALESCE(SUM(messages_published), 0),
                COALESCE(SUM(CASE WHEN sources_failed > 0 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(sources_failed), 0),
                MAX(created_at)
            FROM run_stats
            WHERE created_at >= ?
            """,
            (since.isoformat(),),
        ).fetchone()
        last_run_at = datetime.fromisoformat(row[7]) if row[7] is not None else None
        return StatsSummary(
            runs=row[0],
            messages_fetched=row[1],
            messages_filtered_out=row[2],
            messages_duplicate=row[3],
            messages_published=row[4],
            runs_with_failures=row[5],
            sources_failed_total=row[6],
            last_run_at=last_run_at,
        )

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._conn.close)
