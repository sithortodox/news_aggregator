from datetime import UTC, datetime, timedelta

import pytest

from news_aggregator.core.models import ProcessedMessage, RawMessage
from news_aggregator.core.simhash import compute_simhash
from news_aggregator.core.text_utils import compute_text_hash, extract_links, normalize_text
from news_aggregator.dedup.simhash_deduplicator import SimHashDeduplicator
from tests.support.in_memory_simhash_index import InMemorySimhashIndex


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


async def test_first_message_is_not_duplicate() -> None:
    dedup = SimHashDeduplicator(InMemorySimhashIndex())
    result = await dedup.check(_make_message("Уникальная новость дня про экономику"))
    assert result.is_duplicate is False


async def test_near_verbatim_repost_is_caught_as_duplicate() -> None:
    index = InMemorySimhashIndex()
    dedup = SimHashDeduplicator(index, max_hamming_distance=5, lookback_hours=12)
    original = _make_message(
        "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании",
        external_id="1",
    )
    await dedup.remember(original)

    reposted = _make_message(
        "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании (РБК)",
        external_id="2",
    )
    result = await dedup.check(reposted)

    assert result.is_duplicate is True
    assert "SimHash" in result.reason


async def test_unrelated_message_is_not_duplicate() -> None:
    index = InMemorySimhashIndex()
    dedup = SimHashDeduplicator(index, max_hamming_distance=5, lookback_hours=12)
    await dedup.remember(
        _make_message("Центробанк повысил ключевую ставку до 18 процентов сегодня утром")
    )

    result = await dedup.check(
        _make_message("Учёные обнаружили новый вид бабочек в тропических лесах Амазонии")
    )

    assert result.is_duplicate is False


async def test_message_outside_lookback_window_is_ignored() -> None:
    index = InMemorySimhashIndex()
    dedup = SimHashDeduplicator(index, max_hamming_distance=5, lookback_hours=12)
    text = normalize_text(
        "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании"
    )
    await index.save_at(
        compute_simhash(text, shingle_size=3),
        source_id="src1",
        external_id="old",
        created_at=datetime.now(UTC) - timedelta(hours=13),
    )

    result = await dedup.check(
        _make_message(
            "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании (РБК)"
        )
    )

    assert result.is_duplicate is False


async def test_message_inside_lookback_window_is_caught() -> None:
    index = InMemorySimhashIndex()
    dedup = SimHashDeduplicator(index, max_hamming_distance=5, lookback_hours=12)
    text = normalize_text(
        "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании"
    )
    await index.save_at(
        compute_simhash(text, shingle_size=3),
        source_id="src1",
        external_id="recent",
        created_at=datetime.now(UTC) - timedelta(hours=11),
    )

    result = await dedup.check(
        _make_message(
            "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании (РБК)"
        )
    )

    assert result.is_duplicate is True


async def test_remember_purges_entries_outside_window() -> None:
    index = InMemorySimhashIndex()
    dedup = SimHashDeduplicator(index, max_hamming_distance=5, lookback_hours=12)
    await index.save_at(
        compute_simhash("что-то старое", shingle_size=3),
        source_id="src1",
        external_id="old",
        created_at=datetime.now(UTC) - timedelta(hours=13),
    )

    await dedup.remember(_make_message("Новое сообщение, которое стоит запомнить"))

    remaining = await index.find_recent(datetime.now(UTC) - timedelta(hours=24))
    assert len(remaining) == 1  # старая запись должна была удалиться


async def test_empty_text_is_never_duplicate_and_does_not_crash_remember() -> None:
    dedup = SimHashDeduplicator(InMemorySimhashIndex())
    empty = _make_message("")

    result = await dedup.check(empty)
    assert result.is_duplicate is False

    await dedup.remember(empty)  # не должно падать


def test_negative_max_hamming_distance_raises() -> None:
    with pytest.raises(ValueError):
        SimHashDeduplicator(InMemorySimhashIndex(), max_hamming_distance=-1)


def test_non_positive_lookback_hours_raises() -> None:
    with pytest.raises(ValueError):
        SimHashDeduplicator(InMemorySimhashIndex(), lookback_hours=0)


def test_invalid_unit_raises() -> None:
    with pytest.raises(ValueError):
        SimHashDeduplicator(InMemorySimhashIndex(), unit="sentence")  # type: ignore[arg-type]


async def test_word_unit_still_catches_near_verbatim_repost() -> None:
    index = InMemorySimhashIndex()
    dedup = SimHashDeduplicator(index, max_hamming_distance=10, lookback_hours=12, unit="word")
    await dedup.remember(
        _make_message(
            "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании",
            external_id="1",
        )
    )

    result = await dedup.check(
        _make_message(
            "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании (РБК)",
            external_id="2",
        )
    )

    assert result.is_duplicate is True


def test_name_property() -> None:
    assert SimHashDeduplicator(InMemorySimhashIndex()).name == "simhash_deduplicator"
