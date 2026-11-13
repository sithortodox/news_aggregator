from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from news_aggregator.core.models import EmbeddingRecord
from news_aggregator.storage.sqlite_embedding_index import SqliteEmbeddingIndex


async def test_saves_and_finds_vectors(tmp_path: Path) -> None:
    db = str(tmp_path / "state.db")
    index = SqliteEmbeddingIndex(db)
    record = EmbeddingRecord(vector=(0.6, 0.8), source_id="naebnet", stems=frozenset({"ливен"}))
    await index.save(record, external_id="17925")
    await index.close()

    reopened = SqliteEmbeddingIndex(db)
    (found,) = await reopened.find_recent(datetime.now(UTC) - timedelta(hours=1))
    await reopened.close()

    assert found.source_id == "naebnet"
    assert found.stems == frozenset({"ливен"})
    assert found.vector == pytest.approx((0.6, 0.8))


async def test_purges_old_records(tmp_path: Path) -> None:
    index = SqliteEmbeddingIndex(str(tmp_path / "state.db"))
    await index.save(EmbeddingRecord((1.0,), "a", frozenset()), external_id="1")

    deleted = await index.purge_older_than(datetime.now(UTC) + timedelta(seconds=1))

    assert deleted == 1
    assert await index.find_recent(datetime.now(UTC) - timedelta(hours=1)) == []
    await index.close()


def test_rejects_unsafe_table_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SqliteEmbeddingIndex(str(tmp_path / "state.db"), table_name="x; DROP TABLE y")
