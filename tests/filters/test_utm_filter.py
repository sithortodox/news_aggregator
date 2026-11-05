from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from types import SimpleNamespace

import pytest

from news_aggregator.core.interfaces import ISourceReader
from news_aggregator.core.models import ProcessedMessage, RawMessage, Source, SourceKind
from news_aggregator.core.pipeline import AggregatorPipeline
from news_aggregator.core.text_utils import compute_text_hash, extract_links, normalize_text
from news_aggregator.dedup.hash_deduplicator import TextHashDeduplicator
from news_aggregator.filters.utm_filter import UtmSourceFilter
from tests.support.in_memory_source_repository import InMemorySourceRepository
from tests.support.in_memory_storage import InMemoryStorage


def _message(text: str, entities: tuple[object, ...] = ()) -> ProcessedMessage:
    normalized = normalize_text(text)
    raw = RawMessage(
        source_id="s",
        external_id="1",
        text=text,
        posted_at=datetime.now(),
        fetched_at=datetime.now(),
        formatting_entities=entities,
    )
    return ProcessedMessage(
        raw=raw,
        normalized_text=normalized,
        text_hash=compute_text_hash(normalized),
        links=extract_links(text),
    )


def _dropped(text: str, entities: tuple[object, ...] = (), **kwargs: object) -> bool:
    return UtmSourceFilter(**kwargs).should_drop(_message(text, entities))[0]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "Скидки тут https://shop.example.com/sale?utm_source=telegram",
        "https://shop.example.com/sale?utm_source=",  # параметр есть, значение пустое
        "https://shop.example.com/sale?a=1&utm_source=tg&b=2",
        "https://shop.example.com/sale?UTM_SOURCE=tg",
        "https://shop.example.com/sale?utm_medium=cpc&UTM_Source=tg",
        "https://shop.example.com/sale#utm_source=tg",
        "https://shop.example.com/sale?x=1;utm_source=tg",
        "shop.example.com/sale?utm_source=tg",  # без схемы
        "http://t.me/channel/42?utm_source=ads",
        "Смотри: https://r.example.com/go?url=https%3A%2F%2Fshop.ru%2F%3Futm_source%3Dtg",
    ],
)
def test_post_with_utm_source_link_is_dropped(text: str) -> None:
    assert _dropped(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Новость без ссылок",
        "Статья https://example.com/news/123",
        "Ссылка с метками, но не источником https://example.com/a?utm_medium=cpc&utm_campaign=x",
        "https://example.com/a?xutm_source=1",  # другое имя параметра
        "https://example.com/utm_source=abc/page",  # в пути, не в запросе
        "Про utm_source=tg в тексте, но без ссылки",
        "https://example.com/a?source=telegram",
    ],
)
def test_post_without_utm_source_link_is_kept(text: str) -> None:
    assert _dropped(text) is False


def test_hyperlink_on_a_word_is_checked_through_formatting_entities() -> None:
    """Рекламную ссылку часто прячут за словом «Подробнее» — в тексте её нет."""
    entity = SimpleNamespace(url="https://shop.example.com/x?utm_source=tg")

    assert _dropped("Подробнее о скидках", entities=(entity,)) is True


def test_clean_hyperlink_entity_is_kept() -> None:
    entity = SimpleNamespace(url="https://example.com/news/1")

    assert _dropped("Подробнее", entities=(entity,)) is False


def test_entities_without_url_or_with_non_string_url_are_ignored() -> None:
    entities = (
        SimpleNamespace(offset=0, length=3),
        SimpleNamespace(url=None),
        SimpleNamespace(url=5),
    )

    assert _dropped("Текст без ссылок", entities=entities) is False


def test_reason_names_param_and_host() -> None:
    drop, reason = UtmSourceFilter().should_drop(
        _message("https://shop.example.com/sale?UTM_SOURCE=tg")
    )

    assert drop is True
    assert reason is not None
    assert "utm_source" in reason
    assert "shop.example.com" in reason


def test_custom_parameter_names_are_respected() -> None:
    text = "https://example.com/a?utm_medium=cpc"

    assert _dropped(text) is False
    assert _dropped(text, param_names=["utm_medium"]) is True
    assert _dropped(text, param_names=["UTM_Medium", "utm_source"]) is True


@pytest.mark.parametrize("names", [[], [""], ["  "]])
def test_empty_parameter_list_is_rejected(names: list[str]) -> None:
    with pytest.raises(ValueError):
        UtmSourceFilter(param_names=names)


def test_name_property() -> None:
    assert UtmSourceFilter().name == "utm_filter"


# ---------------------------------------------------- сквозной прогон через пайплайн


class _Reader(ISourceReader):
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts

    def supports(self, source: Source) -> bool:
        return True

    async def read_new_messages(
        self, source: Source, since_external_id: str | None
    ) -> AsyncIterator[RawMessage]:
        for i, text in enumerate(self._texts, start=1):
            yield RawMessage(
                source_id=source.id,
                external_id=str(i),
                text=text,
                posted_at=datetime.now(),
                fetched_at=datetime.now(),
            )


class _Publisher:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def publish(self, message: ProcessedMessage) -> None:
        self.texts.append(message.raw.text)


async def test_pipeline_does_not_publish_utm_posts_and_counts_them_as_filtered() -> None:
    storage = InMemoryStorage()
    publisher = _Publisher()
    source = Source(id="s", kind=SourceKind.FAKE, identifier="s", display_name="s")
    reader = _Reader(
        [
            "Распродажа до конца недели https://shop.example.com/sale?utm_source=telegram",
            "Центробанк повысил ключевую ставку до восемнадцати процентов годовых",
        ]
    )
    pipeline = AggregatorPipeline(
        source_repository=InMemorySourceRepository([source]),
        readers=[reader],
        filters=[UtmSourceFilter()],
        deduplicators=[TextHashDeduplicator(storage)],
        enrichers=[],
        publishers=[publisher],  # type: ignore[list-item]
        storage=storage,
        dry_run=False,
    )

    stats = await pipeline.run_once()

    assert stats.messages_fetched == 2
    assert stats.messages_filtered_out == 1
    assert stats.messages_published == 1
    assert publisher.texts == [
        "Центробанк повысил ключевую ставку до восемнадцати процентов годовых"
    ]
