from datetime import UTC, datetime, timedelta
from pathlib import Path

from news_aggregator.core.models import FileHashRecord
from news_aggregator.storage.sqlite_file_hash_index import SqliteFileHashIndex


async def test_full_hash_and_stems_survive_roundtrip(tmp_path: Path) -> None:
    index = SqliteFileHashIndex(str(tmp_path / "state.db"))
    record = FileHashRecord(
        file_hash="a" * 64, source_id="whackdoor", stems=frozenset({"суд", "шпион"})
    )

    await index.save(record, external_id="32871")
    found = await index.find_recent(datetime.now(UTC) - timedelta(hours=1))

    assert found == [record]
    await index.close()


async def test_purge_removes_only_old_records(tmp_path: Path) -> None:
    index = SqliteFileHashIndex(str(tmp_path / "state.db"))
    await index.save(FileHashRecord("a" * 64, "a", frozenset()), external_id="1")

    assert await index.purge_older_than(datetime.now(UTC) - timedelta(hours=1)) == 0
    assert await index.purge_older_than(datetime.now(UTC) + timedelta(seconds=1)) == 1
    assert await index.find_recent(datetime.now(UTC) - timedelta(days=1)) == []
    await index.close()
