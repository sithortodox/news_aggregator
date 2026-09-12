from collections.abc import AsyncIterator
from datetime import datetime

from news_aggregator.core.interfaces import ISourceReader
from news_aggregator.core.models import ProcessedMessage, RawMessage, Source, SourceKind
from news_aggregator.core.pipeline import AggregatorPipeline
from news_aggregator.dedup.hash_deduplicator import TextHashDeduplicator
from news_aggregator.filters.length_filter import LengthFilter
from tests.support.in_memory_source_repository import InMemorySourceRepository
from tests.support.in_memory_storage import InMemoryStorage


class _ScriptedReader(ISourceReader):
    """Тестовый reader, отдающий заранее заданный список сообщений."""

    def __init__(self, source_id: str, messages: list[RawMessage]) -> None:
        self._source_id = source_id
        self._messages = messages

    def supports(self, source: Source) -> bool:
        return source.id == self._source_id

    async def read_new_messages(
        self, source: Source, since_external_id: str | None
    ) -> AsyncIterator[RawMessage]:
        for msg in self._messages:
            if since_external_id is not None and msg.external_id <= since_external_id:
                continue
            yield msg


class _FailingReader(ISourceReader):
    """Тестовый reader, всегда падающий при чтении (для проверки изоляции ошибок)."""

    def supports(self, source: Source) -> bool:
        return source.id == "failing_source"

    async def read_new_messages(
        self, source: Source, since_external_id: str | None
    ) -> AsyncIterator[RawMessage]:
        raise RuntimeError("источник недоступен")
        yield  # pragma: no cover - недостижимо, нужно для async generator


class _RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[ProcessedMessage] = []

    async def publish(self, message: ProcessedMessage) -> None:
        self.published.append(message)


def _msg(source_id: str, external_id: str, text: str) -> RawMessage:
    return RawMessage(
        source_id=source_id,
        external_id=external_id,
        text=text,
        posted_at=datetime.now(),
        fetched_at=datetime.now(),
    )


def _make_pipeline(
    reader: ISourceReader,
    sources: list[Source],
    publisher: _RecordingPublisher,
    storage: InMemoryStorage,
    dry_run: bool = False,
    source_repository: InMemorySourceRepository | None = None,
) -> AggregatorPipeline:
    return AggregatorPipeline(
        source_repository=source_repository or InMemorySourceRepository(sources),
        readers=[reader],
        filters=[LengthFilter(min_length=5)],
        deduplicators=[TextHashDeduplicator(storage)],
        enrichers=[],
        publishers=[publisher],
        storage=storage,
        dry_run=dry_run,
    )


async def test_unique_messages_are_published() -> None:
    source = Source(
        id="src1", kind=SourceKind.FAKE, identifier="src1", display_name="Src1"
    )
    reader = _ScriptedReader(
        "src1",
        [
            _msg("src1", "1", "Первая уникальная новость дня"),
            _msg("src1", "2", "Вторая уникальная новость дня"),
        ],
    )
    publisher = _RecordingPublisher()
    storage = InMemoryStorage()
    pipeline = _make_pipeline(reader, [source], publisher, storage)

    stats = await pipeline.run_once()

    assert stats.messages_fetched == 2
    assert stats.messages_published == 2
    assert stats.messages_duplicate == 0
    assert len(publisher.published) == 2


async def test_duplicate_messages_are_not_published_twice() -> None:
    source = Source(
        id="src1", kind=SourceKind.FAKE, identifier="src1", display_name="Src1"
    )
    reader = _ScriptedReader(
        "src1",
        [
            _msg("src1", "1", "Одна и та же новость"),
            _msg("src1", "2", "одна и та же новость!!!"),
        ],
    )
    publisher = _RecordingPublisher()
    storage = InMemoryStorage()
    pipeline = _make_pipeline(reader, [source], publisher, storage)

    stats = await pipeline.run_once()

    assert stats.messages_fetched == 2
    assert stats.messages_published == 1
    assert stats.messages_duplicate == 1
    assert len(publisher.published) == 1


async def test_short_messages_are_filtered_out() -> None:
    source = Source(
        id="src1", kind=SourceKind.FAKE, identifier="src1", display_name="Src1"
    )
    reader = _ScriptedReader("src1", [_msg("src1", "1", "коро")])
    publisher = _RecordingPublisher()
    storage = InMemoryStorage()
    pipeline = _make_pipeline(reader, [source], publisher, storage)

    stats = await pipeline.run_once()

    assert stats.messages_filtered_out == 1
    assert stats.messages_published == 0
    assert len(publisher.published) == 0


async def test_dry_run_does_not_call_publisher() -> None:
    source = Source(
        id="src1", kind=SourceKind.FAKE, identifier="src1", display_name="Src1"
    )
    reader = _ScriptedReader("src1", [_msg("src1", "1", "Достаточно длинная новость")])
    publisher = _RecordingPublisher()
    storage = InMemoryStorage()
    pipeline = _make_pipeline(reader, [source], publisher, storage, dry_run=True)

    stats = await pipeline.run_once()

    assert stats.messages_published == 1  # учитывается в статистике
    assert len(publisher.published) == 0  # но реально не публикуется


async def test_cursor_is_saved_and_used_on_next_run() -> None:
    source = Source(
        id="src1", kind=SourceKind.FAKE, identifier="src1", display_name="Src1"
    )
    all_messages = [
        _msg("src1", "1", "Первая длинная новость"),
        _msg("src1", "2", "Вторая длинная новость"),
    ]
    reader = _ScriptedReader("src1", all_messages)
    publisher = _RecordingPublisher()
    storage = InMemoryStorage()
    pipeline = _make_pipeline(reader, [source], publisher, storage)

    await pipeline.run_once()
    assert len(publisher.published) == 2

    # Второй прогон с тем же reader не должен повторно отдать уже виденные
    # external_id благодаря сохранённому курсору.
    stats2 = await pipeline.run_once()
    assert stats2.messages_fetched == 0


async def test_failing_source_does_not_stop_other_sources() -> None:
    good_source = Source(
        id="src1", kind=SourceKind.FAKE, identifier="src1", display_name="Src1"
    )
    bad_source = Source(
        id="failing_source",
        kind=SourceKind.FAKE,
        identifier="failing_source",
        display_name="Failing",
    )
    good_reader = _ScriptedReader("src1", [_msg("src1", "1", "Рабочая длинная новость")])
    bad_reader = _FailingReader()
    publisher = _RecordingPublisher()
    storage = InMemoryStorage()
    pipeline = AggregatorPipeline(
        source_repository=InMemorySourceRepository([bad_source, good_source]),
        readers=[good_reader, bad_reader],
        filters=[LengthFilter(min_length=5)],
        deduplicators=[TextHashDeduplicator(storage)],
        enrichers=[],
        publishers=[publisher],
        storage=storage,
        dry_run=False,
    )

    stats = await pipeline.run_once()

    assert stats.sources_failed == 1
    assert len(stats.errors) == 1
    assert stats.messages_published == 1
    assert len(publisher.published) == 1


async def test_source_without_matching_reader_is_reported_as_error() -> None:
    source = Source(
        id="unknown_source",
        kind=SourceKind.FAKE,
        identifier="unknown_source",
        display_name="Unknown",
    )
    reader = _ScriptedReader("src1", [])
    publisher = _RecordingPublisher()
    storage = InMemoryStorage()
    pipeline = _make_pipeline(reader, [source], publisher, storage)

    stats = await pipeline.run_once()

    assert stats.sources_failed == 1
    assert len(stats.errors) == 1


async def test_disabled_sources_are_skipped() -> None:
    source = Source(
        id="src1",
        kind=SourceKind.FAKE,
        identifier="src1",
        display_name="Src1",
        enabled=False,
    )
    reader = _ScriptedReader("src1", [_msg("src1", "1", "Не должно быть обработано")])
    publisher = _RecordingPublisher()
    storage = InMemoryStorage()
    pipeline = _make_pipeline(reader, [source], publisher, storage)

    stats = await pipeline.run_once()

    assert stats.sources_total == 0
    assert stats.messages_fetched == 0


async def test_source_added_after_pipeline_creation_is_picked_up_next_run() -> None:
    """Источник, добавленный в репозиторий уже ПОСЛЕ создания пайплайна
    (как это будет делать бот через /add), должен быть подхвачен на
    следующем run_once() без пересоздания AggregatorPipeline."""
    reader = _ScriptedReader("src2", [_msg("src2", "1", "Новый канал уже работает")])
    publisher = _RecordingPublisher()
    storage = InMemoryStorage()
    repository = InMemorySourceRepository([])  # изначально пусто
    pipeline = _make_pipeline(
        reader, [], publisher, storage, source_repository=repository
    )

    stats_before = await pipeline.run_once()
    assert stats_before.sources_total == 0

    # "Бот" добавляет канал в тот же репозиторий, которым уже пользуется pipeline.
    new_source = Source(
        id="src2", kind=SourceKind.FAKE, identifier="src2", display_name="Src2"
    )
    assert await repository.add_source(new_source) is True

    stats_after = await pipeline.run_once()

    assert stats_after.sources_total == 1
    assert stats_after.messages_published == 1
    assert len(publisher.published) == 1
