from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from news_aggregator.storage.sqlite_simhash_index import SqliteSimhashIndex


@pytest.fixture
async def index(tmp_path: Path):
    db_path = tmp_path / "simhash.db"
    idx = SqliteSimhashIndex(db_path=str(db_path))
    yield idx
    await idx.close()


async def test_empty_index_has_no_recent_entries(index: SqliteSimhashIndex) -> None:
    since = datetime.now(UTC) - timedelta(hours=1)
    assert await index.find_recent(since) == []


async def test_save_and_find_recent(index: SqliteSimhashIndex) -> None:
    await index.save(12345, source_id="s1", external_id="1")

    since = datetime.now(UTC) - timedelta(hours=1)
    assert await index.find_recent(since) == [12345]


async def test_find_recent_handles_full_64_bit_range(index: SqliteSimhashIndex) -> None:
    # Старший бит установлен - критично для знакового хранения в SQLite INTEGER.
    await index.save(2**63, source_id="s1", external_id="1")
    await index.save(2**64 - 1, source_id="s1", external_id="2")
    await index.save(0, source_id="s1", external_id="3")

    since = datetime.now(UTC) - timedelta(hours=1)
    assert sorted(await index.find_recent(since)) == sorted([2**63, 2**64 - 1, 0])


async def test_find_recent_excludes_entries_before_since(index: SqliteSimhashIndex) -> None:
    await index.save(111, source_id="s1", external_id="1")

    future = datetime.now(UTC) + timedelta(hours=1)
    assert await index.find_recent(future) == []


async def test_purge_older_than_removes_only_old_entries(index: SqliteSimhashIndex) -> None:
    await index.save(111, source_id="s1", external_id="1")

    now = datetime.now(UTC)
    deleted = await index.purge_older_than(now - timedelta(hours=1))
    assert deleted == 0
    assert await index.find_recent(now - timedelta(hours=1)) == [111]

    deleted = await index.purge_older_than(now + timedelta(hours=1))
    assert deleted == 1
    assert await index.find_recent(now - timedelta(hours=1)) == []


async def test_data_survives_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "simhash.db"
    index1 = SqliteSimhashIndex(db_path=str(db_path))
    await index1.save(42, source_id="s1", external_id="1")
    await index1.close()

    index2 = SqliteSimhashIndex(db_path=str(db_path))
    since = datetime.now(UTC) - timedelta(hours=1)
    assert await index2.find_recent(since) == [42]
    await index2.close()
