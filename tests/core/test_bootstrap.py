from news_aggregator.bootstrap import build_registry
from news_aggregator.dedup.hash_deduplicator import TextHashDeduplicator
from news_aggregator.filters.length_filter import LengthFilter
from news_aggregator.publishers.console_publisher import ConsolePublisher
from news_aggregator.sources.fake_source import FakeSourceReader
from news_aggregator.storage.sqlite_source_repository import SqliteSourceRepository
from news_aggregator.storage.sqlite_storage import SqliteStorage
from tests.support.in_memory_storage import InMemoryStorage


def test_registry_has_core_mvp_components() -> None:
    registry = build_registry()

    assert "length_filter" in registry.filters.available()
    assert "ad_filter" in registry.filters.available()
    assert "text_hash_deduplicator" in registry.deduplicators.available()
    assert "link_deduplicator" in registry.deduplicators.available()
    assert "console_publisher" in registry.publishers.available()
    assert "fake" in registry.sources.available()
    assert "sqlite" in registry.storages.available()
    assert "sqlite" in registry.source_repositories.available()


def test_registry_creates_working_instances() -> None:
    registry = build_registry()

    assert isinstance(registry.filters.create("length_filter", min_length=10), LengthFilter)
    assert isinstance(
        registry.deduplicators.create("text_hash_deduplicator", storage=InMemoryStorage()),
        TextHashDeduplicator,
    )
    assert isinstance(registry.publishers.create("console_publisher"), ConsolePublisher)
    assert isinstance(registry.sources.create("fake"), FakeSourceReader)


def test_registry_creates_sqlite_storage(tmp_path) -> None:  # type: ignore[no-untyped-def]
    registry = build_registry()

    storage = registry.storages.create("sqlite", db_path=str(tmp_path / "test.db"))

    assert isinstance(storage, SqliteStorage)


def test_registry_creates_sqlite_source_repository(tmp_path) -> None:  # type: ignore[no-untyped-def]
    registry = build_registry()

    repository = registry.source_repositories.create("sqlite", db_path=str(tmp_path / "sources.db"))

    assert isinstance(repository, SqliteSourceRepository)
