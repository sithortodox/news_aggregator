from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from news_aggregator.core.models import PipelineStats
from news_aggregator.storage.sqlite_run_stats import SqliteRunStatsStore

NOW = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)


def _stats(
    fetched: int = 0,
    filtered: int = 0,
    duplicate: int = 0,
    published: int = 0,
    failed: int = 0,
    total: int = 5,
) -> PipelineStats:
    return PipelineStats(
        sources_total=total,
        sources_failed=failed,
        messages_fetched=fetched,
        messages_filtered_out=filtered,
        messages_duplicate=duplicate,
        messages_published=published,
    )


@pytest.fixture
async def store(tmp_path: Path):
    s = SqliteRunStatsStore(db_path=str(tmp_path / "state.db"))
    yield s
    await s.close()


async def test_empty_store_gives_zero_summary(store: SqliteRunStatsStore) -> None:
    summary = await store.summarize(NOW - timedelta(hours=1))

    assert summary.runs == 0
    assert summary.messages_fetched == 0
    assert summary.last_run_at is None


async def test_summarize_sums_counters_across_runs(store: SqliteRunStatsStore) -> None:
    await store.record(_stats(fetched=3, filtered=1, duplicate=1, published=1), at=NOW)
    await store.record(_stats(fetched=10, filtered=4, duplicate=2, published=4), at=NOW)

    summary = await store.summarize(NOW - timedelta(hours=1))

    assert summary.runs == 2
    assert summary.messages_fetched == 13
    assert summary.messages_filtered_out == 5
    assert summary.messages_duplicate == 3
    assert summary.messages_published == 5


async def test_summarize_counts_runs_with_failures(store: SqliteRunStatsStore) -> None:
    await store.record(_stats(failed=0), at=NOW)
    await store.record(_stats(failed=2), at=NOW)
    await store.record(_stats(failed=1), at=NOW)

    summary = await store.summarize(NOW - timedelta(hours=1))

    assert summary.runs == 3
    assert summary.runs_with_failures == 2
    assert summary.sources_failed_total == 3


async def test_summarize_excludes_runs_before_window(store: SqliteRunStatsStore) -> None:
    await store.record(_stats(published=100), at=NOW - timedelta(hours=3))
    await store.record(_stats(published=2), at=NOW - timedelta(minutes=30))

    summary = await store.summarize(NOW - timedelta(hours=1))

    assert summary.runs == 1
    assert summary.messages_published == 2


async def test_summarize_window_start_is_inclusive(store: SqliteRunStatsStore) -> None:
    boundary = NOW - timedelta(hours=1)
    await store.record(_stats(published=1), at=boundary)

    assert (await store.summarize(boundary)).runs == 1


async def test_last_run_at_is_the_newest_run_in_window(store: SqliteRunStatsStore) -> None:
    await store.record(_stats(), at=NOW - timedelta(minutes=50))
    await store.record(_stats(), at=NOW - timedelta(minutes=5))
    await store.record(_stats(), at=NOW - timedelta(minutes=20))

    summary = await store.summarize(NOW - timedelta(hours=1))

    assert summary.last_run_at == NOW - timedelta(minutes=5)


async def test_record_purges_runs_older_than_retention(tmp_path: Path) -> None:
    store = SqliteRunStatsStore(db_path=str(tmp_path / "state.db"), retention_days=2)
    try:
        await store.record(_stats(published=1), at=NOW - timedelta(days=5))
        await store.record(_stats(published=1), at=NOW - timedelta(days=1))
        await store.record(_stats(published=1), at=NOW)

        everything = await store.summarize(NOW - timedelta(days=30))

        assert everything.runs == 2  # пятидневной давности удалена
    finally:
        await store.close()


async def test_data_survives_reopen(tmp_path: Path) -> None:
    db = str(tmp_path / "state.db")
    first = SqliteRunStatsStore(db_path=db)
    await first.record(_stats(published=7))
    await first.close()

    second = SqliteRunStatsStore(db_path=db)
    try:
        summary = await second.summarize(datetime.now(UTC) - timedelta(hours=1))
        assert summary.messages_published == 7
    finally:
        await second.close()


def test_non_positive_retention_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SqliteRunStatsStore(db_path=str(tmp_path / "state.db"), retention_days=0)
