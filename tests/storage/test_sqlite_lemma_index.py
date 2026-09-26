from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from news_aggregator.storage.sqlite_lemma_index import SqliteLemmaIndex


@pytest.fixture
async def index(tmp_path: Path):
    db_path = tmp_path / "lemmas.db"
    idx = SqliteLemmaIndex(db_path=str(db_path))
    yield idx
    await idx.close()


async def test_empty_index_has_no_recent_entries(index: SqliteLemmaIndex) -> None:
    since = datetime.now(UTC) - timedelta(hours=1)
    assert await index.find_recent(since) == []


async def test_save_and_find_recent(index: SqliteLemmaIndex) -> None:
    lemmas = frozenset({"ставка", "центробанк", "18"})
    await index.save(lemmas, source_id="s1", external_id="1")

    since = datetime.now(UTC) - timedelta(hours=1)
    assert await index.find_recent(since) == [lemmas]


async def test_find_recent_excludes_entries_before_since(index: SqliteLemmaIndex) -> None:
    await index.save(frozenset({"а", "б"}), source_id="s1", external_id="1")

    future = datetime.now(UTC) + timedelta(hours=1)
    assert await index.find_recent(future) == []


async def test_purge_older_than_removes_only_old_entries(index: SqliteLemmaIndex) -> None:
    await index.save(frozenset({"а", "б"}), source_id="s1", external_id="1")

    now = datetime.now(UTC)
    deleted = await index.purge_older_than(now - timedelta(hours=1))
    assert deleted == 0
    assert await index.find_recent(now - timedelta(hours=1)) == [frozenset({"а", "б"})]

    deleted = await index.purge_older_than(now + timedelta(hours=1))
    assert deleted == 1
    assert await index.find_recent(now - timedelta(hours=1)) == []


async def test_data_survives_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "lemmas.db"
    index1 = SqliteLemmaIndex(db_path=str(db_path))
    await index1.save(frozenset({"новость", "сегодня"}), source_id="s1", external_id="1")
    await index1.close()

    index2 = SqliteLemmaIndex(db_path=str(db_path))
    since = datetime.now(UTC) - timedelta(hours=1)
    assert await index2.find_recent(since) == [frozenset({"новость", "сегодня"})]
    await index2.close()


async def test_custom_table_name_isolates_entries(tmp_path: Path) -> None:
    """Два индекса с разными table_name на одной базе не должны видеть
    записи друг друга — иначе смешаются разные наборы лемм разных
    конфигураций paraphrase_deduplicator."""
    db_path = tmp_path / "shared.db"
    index_a = SqliteLemmaIndex(db_path=str(db_path), table_name="lemmas_a")
    index_b = SqliteLemmaIndex(db_path=str(db_path), table_name="lemmas_b")

    await index_a.save(frozenset({"а"}), source_id="s1", external_id="1")

    since = datetime.now(UTC) - timedelta(hours=1)
    assert await index_a.find_recent(since) == [frozenset({"а"})]
    assert await index_b.find_recent(since) == []

    await index_a.close()
    await index_b.close()


def test_invalid_table_name_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SqliteLemmaIndex(db_path=str(tmp_path / "x.db"), table_name="1invalid; DROP TABLE x")


def test_table_name_with_semicolon_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SqliteLemmaIndex(db_path=str(tmp_path / "x.db"), table_name="ok_name; --")
