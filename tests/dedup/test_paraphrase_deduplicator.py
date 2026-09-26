from datetime import UTC, datetime, timedelta

import pytest

from news_aggregator.core.lemmatize import extract_lemma_set
from news_aggregator.core.models import ProcessedMessage, RawMessage
from news_aggregator.core.text_utils import compute_text_hash, extract_links, normalize_text
from news_aggregator.dedup.paraphrase_deduplicator import ParaphraseDeduplicator
from tests.support.in_memory_lemma_index import InMemoryLemmaIndex


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


_ORIGINAL = (
    "Центробанк России повысил ключевую ставку до 18 процентов на "
    "сегодняшнем заседании совета директоров"
)
_PARAPHRASE = "Сегодня ЦБ РФ принял решение о повышении ключевой ставки — теперь она составляет 18%"
_UNRELATED = "Учёные обнаружили новый вид бабочек в тропических лесах Амазонии"


async def test_first_message_is_not_duplicate() -> None:
    dedup = ParaphraseDeduplicator(InMemoryLemmaIndex())
    result = await dedup.check(_make_message(_ORIGINAL))
    assert result.is_duplicate is False


async def test_paraphrased_repost_from_different_channel_is_caught() -> None:
    """Ключевой сценарий: SimHash такое не ловит (разная формулировка), а
    этот дедупликатор — должен, за счёт сравнения по леммам."""
    index = InMemoryLemmaIndex()
    dedup = ParaphraseDeduplicator(index, min_jaccard=0.15, lookback_hours=24)
    await dedup.remember(_make_message(_ORIGINAL, external_id="1"))

    result = await dedup.check(_make_message(_PARAPHRASE, external_id="2"))

    assert result.is_duplicate is True
    assert "Жаккар" in result.reason


async def test_unrelated_message_is_not_duplicate() -> None:
    index = InMemoryLemmaIndex()
    dedup = ParaphraseDeduplicator(index, min_jaccard=0.15, lookback_hours=24)
    await dedup.remember(_make_message(_ORIGINAL))

    result = await dedup.check(_make_message(_UNRELATED))

    assert result.is_duplicate is False


async def test_message_shorter_than_min_lemmas_is_not_duplicate_without_querying() -> None:
    """Короткие тексты (мало значимых слов после лемматизации) не
    сравниваются вовсе — иначе Жаккар на паре токенов даёт случайные
    ложные срабатывания."""
    index = InMemoryLemmaIndex()
    dedup = ParaphraseDeduplicator(index, min_jaccard=0.01, min_lemmas=4)
    await dedup.remember(_make_message(_ORIGINAL))

    result = await dedup.check(_make_message("ставка выросла"))  # 2 значимых леммы

    assert result.is_duplicate is False
    assert "мало значимых слов" in result.reason


async def test_message_outside_lookback_window_is_ignored() -> None:
    index = InMemoryLemmaIndex()
    dedup = ParaphraseDeduplicator(index, min_jaccard=0.15, lookback_hours=24)
    lemmas = extract_lemma_set(normalize_text(_ORIGINAL))
    await index.save_at(
        lemmas,
        source_id="src1",
        external_id="old",
        created_at=datetime.now(UTC) - timedelta(hours=25),
    )

    result = await dedup.check(_make_message(_PARAPHRASE))

    assert result.is_duplicate is False


async def test_message_inside_lookback_window_is_caught() -> None:
    index = InMemoryLemmaIndex()
    dedup = ParaphraseDeduplicator(index, min_jaccard=0.15, lookback_hours=24)
    lemmas = extract_lemma_set(normalize_text(_ORIGINAL))
    await index.save_at(
        lemmas,
        source_id="src1",
        external_id="recent",
        created_at=datetime.now(UTC) - timedelta(hours=20),
    )

    result = await dedup.check(_make_message(_PARAPHRASE))

    assert result.is_duplicate is True


async def test_remember_purges_entries_outside_window() -> None:
    index = InMemoryLemmaIndex()
    dedup = ParaphraseDeduplicator(index, lookback_hours=24)
    await index.save_at(
        extract_lemma_set("что-то совсем старое и никому не нужное"),
        source_id="src1",
        external_id="old",
        created_at=datetime.now(UTC) - timedelta(hours=25),
    )

    await dedup.remember(_make_message(_ORIGINAL))

    remaining = await index.find_recent(datetime.now(UTC) - timedelta(hours=48))
    assert len(remaining) == 1  # старая запись должна была удалиться


async def test_empty_text_is_never_duplicate_and_does_not_crash_remember() -> None:
    dedup = ParaphraseDeduplicator(InMemoryLemmaIndex())
    empty = _make_message("")

    result = await dedup.check(empty)
    assert result.is_duplicate is False

    await dedup.remember(empty)  # не должно падать


def test_invalid_min_jaccard_raises() -> None:
    with pytest.raises(ValueError):
        ParaphraseDeduplicator(InMemoryLemmaIndex(), min_jaccard=0.0)
    with pytest.raises(ValueError):
        ParaphraseDeduplicator(InMemoryLemmaIndex(), min_jaccard=1.5)


def test_non_positive_lookback_hours_raises() -> None:
    with pytest.raises(ValueError):
        ParaphraseDeduplicator(InMemoryLemmaIndex(), lookback_hours=0)


def test_non_positive_min_lemmas_raises() -> None:
    with pytest.raises(ValueError):
        ParaphraseDeduplicator(InMemoryLemmaIndex(), min_lemmas=0)


def test_name_property() -> None:
    assert ParaphraseDeduplicator(InMemoryLemmaIndex()).name == "paraphrase_deduplicator"
