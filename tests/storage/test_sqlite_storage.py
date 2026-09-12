from pathlib import Path

import pytest

from news_aggregator.storage.sqlite_storage import SqliteStorage


@pytest.fixture
async def storage(tmp_path: Path):
    db_path = tmp_path / "test_state.db"
    storage = SqliteStorage(db_path=str(db_path))
    yield storage
    await storage.close()


async def test_cursor_roundtrip(storage: SqliteStorage) -> None:
    assert await storage.get_last_external_id("src1") is None

    await storage.set_last_external_id("src1", "100")
    assert await storage.get_last_external_id("src1") == "100"

    await storage.set_last_external_id("src1", "200")
    assert await storage.get_last_external_id("src1") == "200"


async def test_cursors_are_independent_per_source(storage: SqliteStorage) -> None:
    await storage.set_last_external_id("src1", "10")
    await storage.set_last_external_id("src2", "20")

    assert await storage.get_last_external_id("src1") == "10"
    assert await storage.get_last_external_id("src2") == "20"


async def test_hash_roundtrip(storage: SqliteStorage) -> None:
    assert await storage.has_hash("abc123") is False

    await storage.save_hash("abc123", source_id="src1", external_id="1")

    assert await storage.has_hash("abc123") is True


async def test_saving_same_hash_twice_does_not_raise(storage: SqliteStorage) -> None:
    await storage.save_hash("abc123", source_id="src1", external_id="1")
    await storage.save_hash("abc123", source_id="src2", external_id="2")

    assert await storage.has_hash("abc123") is True


async def test_link_roundtrip(storage: SqliteStorage) -> None:
    assert await storage.has_link("https://example.com/x") is False

    await storage.save_link("https://example.com/x", source_id="src1", external_id="1")

    assert await storage.has_link("https://example.com/x") is True


async def test_creates_db_file_and_parent_dirs(tmp_path: Path) -> None:
    nested_path = tmp_path / "nested" / "dir" / "state.db"
    storage = SqliteStorage(db_path=str(nested_path))
    try:
        assert nested_path.exists()
    finally:
        await storage.close()
