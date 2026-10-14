"""Учёт сбоев и ограничения частоты запросов (FloodWait) в AggregatorPipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from news_aggregator.core.errors import RateLimitedError
from news_aggregator.core.interfaces import ISourceReader
from news_aggregator.core.models import ProcessedMessage, RawMessage, Source, SourceKind
from news_aggregator.core.pipeline import AggregatorPipeline
from news_aggregator.dedup.hash_deduplicator import TextHashDeduplicator
from news_aggregator.filters.length_filter import LengthFilter
from tests.support.in_memory_source_repository import InMemorySourceRepository
from tests.support.in_memory_storage import InMemoryStorage


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Reader(ISourceReader):
    """Читает сообщения из словаря; для источника из `errors` бросает исключение."""

    def __init__(self, messages: dict[str, list[RawMessage]]) -> None:
        self.messages = messages
        self.errors: dict[str, Exception] = {}
        self.calls: list[str] = []

    def supports(self, source: Source) -> bool:
        return source.kind == SourceKind.FAKE

    async def read_new_messages(
        self, source: Source, since_external_id: str | None
    ) -> AsyncIterator[RawMessage]:
        self.calls.append(source.id)
        if source.id in self.errors:
            raise self.errors[source.id]
        for msg in self.messages.get(source.id, []):
            if since_external_id is not None and int(msg.external_id) <= int(since_external_id):
                continue
            yield msg


class _Publisher:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.fail_with: Exception | None = None
        self.fail_on_calls: set[int] = set()  # номера вызовов (с 1), на которых падать
        self.calls = 0

    async def publish(self, message: ProcessedMessage) -> None:
        self.calls += 1
        if self.fail_with is not None and (
            not self.fail_on_calls or self.calls in self.fail_on_calls
        ):
            raise self.fail_with
        self.texts.append(message.raw.text)


def _src(source_id: str) -> Source:
    return Source(id=source_id, kind=SourceKind.FAKE, identifier=source_id, display_name=source_id)


def _msg(source_id: str, external_id: int, text: str) -> RawMessage:
    return RawMessage(
        source_id=source_id,
        external_id=str(external_id),
        text=text,
        posted_at=datetime.now(),
        fetched_at=datetime.now(),
    )


def _pipeline(
    reader: _Reader,
    source_ids: list[str],
    publishers: list[_Publisher],
    *,
    storage: InMemoryStorage | None = None,
    clock: _Clock | None = None,
    dry_run: bool = False,
) -> tuple[AggregatorPipeline, InMemoryStorage]:
    storage = storage or InMemoryStorage()
    pipeline = AggregatorPipeline(
        source_repository=InMemorySourceRepository([_src(i) for i in source_ids]),
        readers=[reader],
        filters=[LengthFilter(min_length=5)],
        deduplicators=[TextHashDeduplicator(storage)],
        enrichers=[],
        publishers=publishers,  # type: ignore[list-item]
        storage=storage,
        dry_run=dry_run,
        clock=clock or _Clock(),
    )
    return pipeline, storage


# ------------------------------------------------------------ учёт по источникам


async def test_failed_and_succeeded_sources_are_tracked_separately() -> None:
    reader = _Reader({"a": [_msg("a", 1, "Новость от источника а")]})
    reader.errors["b"] = RuntimeError("boom")
    pipeline, _ = _pipeline(reader, ["a", "b"], [_Publisher()])

    stats = await pipeline.run_once()

    assert stats.succeeded_sources == ["a"]
    assert stats.failed_sources == ["b"]
    assert stats.sources_failed == 1
    assert "RuntimeError: boom" in stats.source_errors["b"]


async def test_source_without_reader_is_a_failed_source_with_reason() -> None:
    reader = _Reader({})
    pipeline, _ = _pipeline(reader, [], [_Publisher()])
    pipeline._source_repository = InMemorySourceRepository(  # type: ignore[assignment]
        [Source(id="tg", kind=SourceKind.TELEGRAM_CHANNEL, identifier="@x", display_name="x")]
    )

    stats = await pipeline.run_once()

    assert stats.failed_sources == ["tg"]
    assert "Нет ридера" in stats.source_errors["tg"]


async def test_long_source_error_text_is_truncated() -> None:
    reader = _Reader({})
    reader.errors["a"] = RuntimeError("x" * 5000)
    pipeline, _ = _pipeline(reader, ["a"], [_Publisher()])

    stats = await pipeline.run_once()

    assert len(stats.source_errors["a"]) <= 300


# --------------------------------------------------------- сбои издателя


async def test_publisher_failure_is_counted_and_not_reported_as_published() -> None:
    reader = _Reader({"a": [_msg("a", 1, "Важная новость дня")]})
    publisher = _Publisher()
    publisher.fail_with = RuntimeError("chat not found")
    pipeline, _ = _pipeline(reader, ["a"], [publisher])

    stats = await pipeline.run_once()

    assert stats.publish_failures == 1
    assert "chat not found" in (stats.publish_error or "")
    assert stats.messages_published == 0
    assert stats.sources_failed == 0  # источник тут ни при чём


async def test_unpublished_message_is_not_remembered_so_a_repost_can_still_go_out() -> None:
    reader = _Reader({"a": [_msg("a", 1, "Важная новость дня")]})
    publisher = _Publisher()
    publisher.fail_with = RuntimeError("временный сбой")
    pipeline, _ = _pipeline(reader, ["a"], [publisher])
    await pipeline.run_once()

    publisher.fail_with = None
    reader.messages["a"].append(_msg("a", 2, "Важная новость дня"))  # тот же текст
    stats = await pipeline.run_once()

    assert stats.messages_duplicate == 0
    assert stats.messages_published == 1
    assert publisher.texts == ["Важная новость дня"]


async def test_one_failed_publish_does_not_stop_the_rest_of_the_batch() -> None:
    reader = _Reader(
        {"a": [_msg("a", 1, "Первая новость дня"), _msg("a", 2, "Вторая новость дня")]}
    )
    publisher = _Publisher()
    publisher.fail_with = RuntimeError("сбой")
    publisher.fail_on_calls = {1}
    pipeline, _ = _pipeline(reader, ["a"], [publisher])

    stats = await pipeline.run_once()

    assert stats.messages_fetched == 2
    assert stats.messages_published == 1
    assert stats.publish_failures == 1
    assert publisher.texts == ["Вторая новость дня"]


async def test_message_published_by_at_least_one_publisher_counts_as_published() -> None:
    reader = _Reader({"a": [_msg("a", 1, "Важная новость дня")]})
    good, bad = _Publisher(), _Publisher()
    bad.fail_with = RuntimeError("упал")
    pipeline, _ = _pipeline(reader, ["a"], [good, bad])

    stats = await pipeline.run_once()

    assert stats.messages_published == 1
    assert stats.publish_failures == 1
    assert good.texts == ["Важная новость дня"]


async def test_dry_run_counts_as_published_without_calling_publishers() -> None:
    reader = _Reader({"a": [_msg("a", 1, "Важная новость дня")]})
    publisher = _Publisher()
    pipeline, _ = _pipeline(reader, ["a"], [publisher], dry_run=True)

    stats = await pipeline.run_once()

    assert stats.messages_published == 1
    assert stats.publish_failures == 0
    assert publisher.calls == 0


# ----------------------------------------------- ограничение частоты (FloodWait)


async def test_reader_rate_limit_is_not_a_source_failure_and_pauses_everything() -> None:
    clock = _Clock()
    reader = _Reader({"a": [], "b": [_msg("b", 1, "Новость от источника б")]})
    reader.errors["a"] = RateLimitedError(120)
    pipeline, _ = _pipeline(reader, ["a", "b"], [_Publisher()], clock=clock)

    stats = await pipeline.run_once()

    assert stats.flood_wait_seconds == 120
    assert stats.sources_failed == 0
    assert stats.failed_sources == []
    assert stats.succeeded_sources == []
    assert reader.calls == ["a"]  # до "b" не дошли: пауза общая


async def test_runs_during_pause_do_not_touch_sources_or_report_a_new_flood_wait() -> None:
    clock = _Clock()
    reader = _Reader({"a": []})
    reader.errors["a"] = RateLimitedError(120)
    pipeline, _ = _pipeline(reader, ["a"], [_Publisher()], clock=clock)
    await pipeline.run_once()

    clock.now += 60
    stats = await pipeline.run_once()

    assert reader.calls == ["a"]  # второй прогон не дёргал источник
    assert stats.flood_wait_seconds == 0
    assert stats.sources_total == 1


async def test_pipeline_resumes_after_pause_expires() -> None:
    clock = _Clock()
    reader = _Reader({"a": [_msg("a", 1, "Новость после паузы")]})
    reader.errors["a"] = RateLimitedError(120)
    publisher = _Publisher()
    pipeline, _ = _pipeline(reader, ["a"], [publisher], clock=clock)
    await pipeline.run_once()

    del reader.errors["a"]
    clock.now += 122  # 120 с + запас 1 с
    stats = await pipeline.run_once()

    assert stats.messages_published == 1
    assert stats.succeeded_sources == ["a"]
    assert publisher.texts == ["Новость после паузы"]


async def test_pause_still_active_just_before_deadline() -> None:
    clock = _Clock()
    reader = _Reader({"a": []})
    reader.errors["a"] = RateLimitedError(100)
    pipeline, _ = _pipeline(reader, ["a"], [_Publisher()], clock=clock)
    await pipeline.run_once()

    clock.now += 100.5  # пауза = 100 + 1 с запаса, ещё не вышла
    await pipeline.run_once()

    assert reader.calls == ["a"]


async def test_publisher_rate_limit_keeps_message_for_retry_without_loss_or_duplicates() -> None:
    clock = _Clock()
    reader = _Reader(
        {"a": [_msg("a", 1, "Первая новость дня"), _msg("a", 2, "Вторая новость дня")]}
    )
    publisher = _Publisher()
    publisher.fail_with = RateLimitedError(30)
    pipeline, storage = _pipeline(reader, ["a"], [publisher], clock=clock)

    first = await pipeline.run_once()

    assert first.flood_wait_seconds == 30
    assert first.messages_published == 0
    assert first.messages_fetched == 0  # не засчитано: сообщение вернётся
    assert await storage.get_last_external_id("a") is None  # курсор не сдвинут

    publisher.fail_with = None
    clock.now += 32
    second = await pipeline.run_once()

    assert second.messages_published == 2
    assert second.messages_fetched == 2
    assert publisher.texts == ["Первая новость дня", "Вторая новость дня"]  # без дублей и потерь


async def test_publisher_rate_limit_on_second_message_keeps_cursor_after_first() -> None:
    clock = _Clock()
    reader = _Reader(
        {"a": [_msg("a", 1, "Первая новость дня"), _msg("a", 2, "Вторая новость дня")]}
    )
    publisher = _Publisher()
    publisher.fail_with = RateLimitedError(30)
    publisher.fail_on_calls = {2}
    pipeline, storage = _pipeline(reader, ["a"], [publisher], clock=clock)

    first = await pipeline.run_once()

    assert first.messages_published == 1
    assert await storage.get_last_external_id("a") == "1"

    publisher.fail_with = None
    clock.now += 32
    await pipeline.run_once()

    assert publisher.texts == ["Первая новость дня", "Вторая новость дня"]


async def test_rate_limit_from_one_of_several_publishers_is_a_failure_not_a_retry() -> None:
    """Если часть издателей уже доставила, повтор сообщения дал бы им дубль."""
    clock = _Clock()
    reader = _Reader({"a": [_msg("a", 1, "Важная новость дня")]})
    good, limited = _Publisher(), _Publisher()
    limited.fail_with = RateLimitedError(50)
    pipeline, _ = _pipeline(reader, ["a"], [good, limited], clock=clock)

    stats = await pipeline.run_once()

    assert stats.messages_published == 1
    assert stats.publish_failures == 1
    assert stats.flood_wait_seconds == 50
    assert good.texts == ["Важная новость дня"]

    clock.now += 10
    await pipeline.run_once()  # пауза всё ещё действует
    assert good.calls == 1


async def test_rate_limit_without_reader_errors_does_not_mark_other_runs_failed() -> None:
    clock = _Clock()
    reader = _Reader({"a": [_msg("a", 1, "Новость до ограничения")]})
    pipeline, _ = _pipeline(reader, ["a"], [_Publisher()], clock=clock)

    stats = await pipeline.run_once()

    assert stats.flood_wait_seconds == 0
    assert stats.failed_sources == []
    assert stats.succeeded_sources == ["a"]
