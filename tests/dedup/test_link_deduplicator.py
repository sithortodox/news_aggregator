from datetime import datetime

from news_aggregator.core.models import ProcessedMessage, RawMessage
from news_aggregator.core.text_utils import compute_text_hash, extract_links, normalize_text
from news_aggregator.dedup.link_deduplicator import LinkDeduplicator
from tests.support.in_memory_storage import InMemoryStorage


def _make_message(text: str, external_id: str = "1") -> ProcessedMessage:
    raw = RawMessage(
        source_id="src1",
        external_id=external_id,
        text=text,
        posted_at=datetime.now(),
        fetched_at=datetime.now(),
    )
    normalized = normalize_text(text)
    return ProcessedMessage(
        raw=raw,
        normalized_text=normalized,
        text_hash=compute_text_hash(normalized),
        links=extract_links(text),
    )


async def test_message_without_links_is_not_duplicate() -> None:
    dedup = LinkDeduplicator(InMemoryStorage())
    result = await dedup.check(_make_message("Просто текст без ссылок"))
    assert result.is_duplicate is False


async def test_message_becomes_duplicate_by_shared_link() -> None:
    storage = InMemoryStorage()
    dedup = LinkDeduplicator(storage)
    first = _make_message("Новость раз https://example.com/article", external_id="1")
    second = _make_message(
        "Совсем другой текст, но та же https://example.com/article", external_id="2"
    )

    await dedup.remember(first)
    result = await dedup.check(second)

    assert result.is_duplicate is True
    assert result.matched_link == "https://example.com/article"


async def test_different_links_are_not_duplicate() -> None:
    storage = InMemoryStorage()
    dedup = LinkDeduplicator(storage)
    await dedup.remember(_make_message("Раз https://example.com/a"))

    result = await dedup.check(_make_message("Два https://example.com/b"))

    assert result.is_duplicate is False


def test_name_property() -> None:
    dedup = LinkDeduplicator(InMemoryStorage())
    assert dedup.name == "link_deduplicator"
