from pathlib import Path

import pytest

from news_aggregator.core.models import Source, SourceKind
from news_aggregator.storage.sqlite_source_repository import SqliteSourceRepository


@pytest.fixture
async def repo(tmp_path: Path):
    db_path = tmp_path / "sources.db"
    repository = SqliteSourceRepository(db_path=str(db_path))
    yield repository
    await repository.close()


def _source(id_: str = "news", identifier: str = "@news", enabled: bool = True) -> Source:
    return Source(
        id=id_,
        kind=SourceKind.TELEGRAM_CHANNEL,
        identifier=identifier,
        display_name=identifier,
        enabled=enabled,
    )


async def test_empty_repository_has_no_sources(repo: SqliteSourceRepository) -> None:
    assert await repo.list_sources() == []


async def test_add_and_list_source(repo: SqliteSourceRepository) -> None:
    source = _source()
    assert await repo.add_source(source) is True

    assert await repo.list_sources() == [source]


async def test_add_duplicate_id_is_rejected(repo: SqliteSourceRepository) -> None:
    await repo.add_source(_source(id_="news", identifier="@news"))

    added = await repo.add_source(_source(id_="news", identifier="@other"))

    assert added is False
    assert len(await repo.list_sources()) == 1


async def test_add_duplicate_identifier_is_rejected(repo: SqliteSourceRepository) -> None:
    await repo.add_source(_source(id_="news", identifier="@news"))

    added = await repo.add_source(_source(id_="news2", identifier="@news"))

    assert added is False
    assert len(await repo.list_sources()) == 1


async def test_remove_source(repo: SqliteSourceRepository) -> None:
    await repo.add_source(_source())

    assert await repo.remove_source("news") is True
    assert await repo.list_sources() == []
    assert await repo.remove_source("news") is False


async def test_set_enabled_toggles_source(repo: SqliteSourceRepository) -> None:
    await repo.add_source(_source(enabled=True))

    assert await repo.set_enabled("news", False) is True
    source = await repo.get_source("news")
    assert source is not None
    assert source.enabled is False

    assert await repo.set_enabled("missing", True) is False


async def test_get_source_returns_none_when_missing(repo: SqliteSourceRepository) -> None:
    assert await repo.get_source("missing") is None
