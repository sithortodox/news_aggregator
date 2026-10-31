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


# ---------------------------------------------------- канонизация и «ссылки-подписи»


async def test_tracking_and_formatting_variants_of_one_article_are_duplicates() -> None:
    storage = InMemoryStorage()
    dedup = LinkDeduplicator(storage)
    await dedup.remember(_make_message("Новость https://example.com/news/123", external_id="1"))

    for variant in (
        "https://www.example.com/news/123/",
        "http://example.com/news/123?utm_source=telegram&utm_medium=social",
        "https://example.com/news/123?fbclid=IwAR0abc#comments",
    ):
        result = await dedup.check(_make_message(f"Другой текст {variant}", external_id="2"))
        assert result.is_duplicate is True, variant


async def test_telegram_post_link_variants_are_duplicates() -> None:
    dedup = LinkDeduplicator(InMemoryStorage())
    await dedup.remember(_make_message("Читать: t.me/some_channel/42", external_id="1"))

    result = await dedup.check(
        _make_message("Читать: https://t.me/some_channel/42?single", external_id="2")
    )

    assert result.is_duplicate is True


async def test_channel_signature_link_does_not_make_every_next_post_a_duplicate() -> None:
    """Раньше подпись «t.me/channel» в каждом посте отбрасывала все посты канала после первого."""
    dedup = LinkDeduplicator(InMemoryStorage())
    await dedup.remember(_make_message("Новость один. Подписаться: https://t.me/mychannel", "1"))

    result = await dedup.check(_make_message("Совсем другая новость. https://t.me/mychannel", "2"))

    assert result.is_duplicate is False


async def test_site_homepage_link_is_not_a_duplicate_marker() -> None:
    dedup = LinkDeduplicator(InMemoryStorage())
    await dedup.remember(_make_message("Новость один. Источник: https://example.com/", "1"))

    result = await dedup.check(_make_message("Новость два. Источник: https://example.com", "2"))

    assert result.is_duplicate is False


async def test_different_articles_on_one_site_are_not_duplicates() -> None:
    dedup = LinkDeduplicator(InMemoryStorage())
    await dedup.remember(_make_message("Раз https://example.com/news/1", "1"))

    result = await dedup.check(_make_message("Два https://example.com/news/2", "2"))

    assert result.is_duplicate is False


async def test_rows_saved_in_raw_form_before_canonicalization_still_match() -> None:
    storage = InMemoryStorage()
    await storage.save_link(
        "https://example.com/news/123?utm_source=x", "src", "0"
    )  # старая запись
    dedup = LinkDeduplicator(storage)

    result = await dedup.check(
        _make_message("Текст https://example.com/news/123?utm_source=x", external_id="1")
    )

    assert result.is_duplicate is True


async def test_remember_stores_canonical_form_only() -> None:
    storage = InMemoryStorage()
    dedup = LinkDeduplicator(storage)

    await dedup.remember(_make_message("https://www.example.com/a/?utm_source=x", "1"))

    assert await storage.has_link("example.com/a") is True
    assert await storage.has_link("https://www.example.com/a/?utm_source=x") is False
