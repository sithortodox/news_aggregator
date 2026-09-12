from datetime import datetime

from news_aggregator.core.models import ProcessedMessage, RawMessage
from news_aggregator.core.text_utils import compute_text_hash, normalize_text
from news_aggregator.dedup.hash_deduplicator import TextHashDeduplicator
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
        raw=raw, normalized_text=normalized, text_hash=compute_text_hash(normalized)
    )


async def test_new_message_is_not_duplicate() -> None:
    dedup = TextHashDeduplicator(InMemoryStorage())
    result = await dedup.check(_make_message("Уникальная новость дня"))
    assert result.is_duplicate is False


async def test_message_becomes_duplicate_after_remember() -> None:
    storage = InMemoryStorage()
    dedup = TextHashDeduplicator(storage)
    message = _make_message("Важная новость про экономику")

    await dedup.remember(message)
    result = await dedup.check(message)

    assert result.is_duplicate is True
    assert result.matched_hash == message.text_hash


async def test_reworded_but_semantically_identical_text_is_duplicate() -> None:
    storage = InMemoryStorage()
    dedup = TextHashDeduplicator(storage)
    first = _make_message("Курс доллара вырос сегодня!", external_id="1")
    second = _make_message("курс доллара вырос сегодня", external_id="2")

    await dedup.remember(first)
    result = await dedup.check(second)

    assert result.is_duplicate is True


async def test_different_text_is_not_duplicate() -> None:
    storage = InMemoryStorage()
    dedup = TextHashDeduplicator(storage)
    await dedup.remember(_make_message("Новость А"))

    result = await dedup.check(_make_message("Совершенно другая новость Б"))

    assert result.is_duplicate is False


def test_name_property() -> None:
    dedup = TextHashDeduplicator(InMemoryStorage())
    assert dedup.name == "text_hash_deduplicator"
