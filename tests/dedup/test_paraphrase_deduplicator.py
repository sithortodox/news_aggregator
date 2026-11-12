from datetime import UTC, datetime, timedelta

import pytest

from news_aggregator.core.lemmatize import extract_lemma_set
from news_aggregator.core.models import ProcessedMessage, RawMessage
from news_aggregator.core.text_utils import compute_text_hash, extract_links, normalize_text
from news_aggregator.dedup.paraphrase_deduplicator import ParaphraseDeduplicator
from tests.support.in_memory_lemma_index import InMemoryLemmaIndex
from tests.support.in_memory_stem_frequency_index import InMemoryStemFrequencyIndex


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


# ------------------------------------------------- основы, минимум общих, вето по числам


def _d(**params: object) -> ParaphraseDeduplicator:
    return ParaphraseDeduplicator(InMemoryLemmaIndex(), **params)  # type: ignore[arg-type]


def _words(suffix: str, count: int) -> set[str]:
    # Номер в НАЧАЛЕ слова: иначе усечение до основы склеило бы все слова в одно.
    return {f"{i:02d}{suffix}" for i in range(count)}


def test_jaccard_threshold_is_inclusive() -> None:
    common = _words("общ", 3)
    a = frozenset(common | _words("аа", 6))
    b = frozenset(common | _words("бб", 6))  # 3 общих из 15 в объединении = ровно 0.2
    dedup = _d(min_jaccard=0.2, stem_length=None, min_shared_lemmas=1, number_veto=False)

    assert dedup._compare(a, b)[0] is True
    assert _d(min_jaccard=0.21, stem_length=None, min_shared_lemmas=1)._compare(a, b)[0] is False


def test_stems_merge_related_word_forms() -> None:
    a = frozenset({"канада", "хоккей", "победить", "финал"})
    b = frozenset({"канадец", "хоккейный", "победа", "финальный"})

    assert _d(stem_length=5, min_shared_lemmas=3)._compare(a, b)[0] is True
    assert _d(stem_length=None, min_shared_lemmas=1)._compare(a, b)[0] is False


def test_stems_do_not_touch_numbers_or_short_words() -> None:
    a = frozenset({"2022", "курс", "доллар"})
    b = frozenset({"2022", "курс", "доллар"})

    assert _d(stem_length=3, min_shared_lemmas=3)._compare(a, b)[0] is True


def test_min_shared_lemmas_blocks_a_lucky_match_on_short_texts() -> None:
    a = frozenset({"ключевой", "ставка", "повысить", "цб"})
    b = frozenset({"ключевой", "ставка", "сохранить", "решение"})  # 2 общих из 6: Жаккар 0.33

    assert _d(min_shared_lemmas=2, number_veto=False)._compare(a, b)[0] is True
    assert _d(min_shared_lemmas=3)._compare(a, b)[0] is False


def test_number_veto_blocks_when_both_have_numbers_and_none_match() -> None:
    a = frozenset({"ставка", "повысить", "ключевой", "цб", "18"})
    b = frozenset({"ставка", "повысить", "ключевой", "цб", "19"})

    assert _d()._compare(a, b)[0] is False
    assert _d(number_veto=False)._compare(a, b)[0] is True


def test_number_veto_allows_a_shared_number() -> None:
    a = frozenset({"ставка", "повысить", "ключевой", "цб", "18", "200"})
    b = frozenset({"ставка", "повысить", "ключевой", "цб", "18"})

    assert _d()._compare(a, b)[0] is True


def test_number_veto_does_not_apply_when_only_one_text_has_numbers() -> None:
    """Пересказ может записать число словами — это не конфликт."""
    a = frozenset({"ставка", "повысить", "ключевой", "цб", "18"})
    b = frozenset({"ставка", "повысить", "ключевой", "цб", "восемнадцать"})

    assert _d()._compare(a, b)[0] is True


def test_min_overlap_is_off_by_default_and_catches_headline_vs_long_text_when_enabled() -> None:
    headline = frozenset({"цб", "повысить", "ключевой", "ставка", "годовой"})
    long_text = frozenset(headline | _words("слово", 25))

    strict = _d(min_jaccard=0.4)
    assert strict._compare(headline, long_text)[0] is False
    assert _d(min_jaccard=0.4, min_overlap=0.8)._compare(headline, long_text)[0] is True


def test_min_overlap_still_requires_minimum_shared_lemmas() -> None:
    small = frozenset({"аа", "бб"})
    big = frozenset(small | _words("слово", 30))

    assert _d(min_jaccard=0.9, min_overlap=0.5, stem_length=None)._compare(small, big)[0] is False


@pytest.mark.parametrize(
    "params",
    [
        {"stem_length": 1},
        {"min_shared_lemmas": 0},
        {"min_overlap": 0.0},
        {"min_overlap": 1.5},
    ],
)
def test_new_parameters_are_validated(params: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _d(**params)


def test_stem_length_none_means_no_truncation() -> None:
    _d(stem_length=None)  # не должно падать


# Регрессия: my_news_agregator_channel 9305 (Бэкдор) и 9308 (1337) — один
# пересказ, общих основ всего три, обычный Жаккар 0.17 их не склеивал.
_BACKDOOR = (
    "⚡️ У Хуанга была и третья любовница — СМИ. Любитель кожаных курток и уже "
    "экс-CEO NVIDIA, похоже, собрал целый гарем из сотрудниц.\n\n👍 Бэкдор"
)
_MEDIA1337 = "У Хуанга нашли ещё одну любовницу, уже третью по счёту — Reuters.\n\n🌒 1337"


def _from_source(text: str, source_id: str, external_id: str) -> ProcessedMessage:
    message = _make_message(text, external_id)
    raw = RawMessage(
        source_id=source_id,
        external_id=external_id,
        text=text,
        posted_at=message.raw.posted_at,
        fetched_at=message.raw.fetched_at,
    )
    return ProcessedMessage(
        raw=raw,
        normalized_text=message.normalized_text,
        text_hash=message.text_hash,
        links=message.links,
    )


def _weighted(
    frequencies: InMemoryStemFrequencyIndex, *, dry_run: bool = False, min_documents: int = 300
) -> ParaphraseDeduplicator:
    return ParaphraseDeduplicator(
        InMemoryLemmaIndex(),
        frequency_index=frequencies,
        weighted_min_jaccard=0.15,
        weighted_min_documents=min_documents,
        weighted_dry_run=dry_run,
    )


async def _publish_then_check(dedup: ParaphraseDeduplicator) -> tuple[bool, str]:
    first = _from_source(_BACKDOOR, "whackdoor", "32919")
    assert (await dedup.check(first)).is_duplicate is False
    await dedup.remember(first)
    result = await dedup.check(_from_source(_MEDIA1337, "media1337", "29138"))
    return result.is_duplicate, result.reason


async def test_plain_jaccard_misses_the_retelling() -> None:
    is_duplicate, _ = await _publish_then_check(ParaphraseDeduplicator(InMemoryLemmaIndex()))
    assert is_duplicate is False


async def test_weighted_rule_catches_retelling_sharing_rare_words() -> None:
    is_duplicate, reason = await _publish_then_check(
        _weighted(InMemoryStemFrequencyIndex(total=1000))
    )

    assert is_duplicate is True
    assert "весовой Жаккар" in reason
    assert "хуанг" in reason


async def test_shared_common_words_weigh_little() -> None:
    # Если эти слова есть в половине постов, совпадение по ним ничего не значит.
    common = {"хуанг": 500, "любов": 500, "трети": 500}

    is_duplicate, _ = await _publish_then_check(
        _weighted(InMemoryStemFrequencyIndex(total=1000, counts=common))
    )

    assert is_duplicate is False


async def test_dry_run_only_reports(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")

    is_duplicate, reason = await _publish_then_check(
        _weighted(InMemoryStemFrequencyIndex(total=1000), dry_run=True)
    )

    assert is_duplicate is False
    assert "пробно" in reason
    assert "Пересказ (пробно, не отброшено): media1337/29138" in caplog.text


async def test_weighted_rule_waits_for_enough_statistics() -> None:
    is_duplicate, _ = await _publish_then_check(
        _weighted(InMemoryStemFrequencyIndex(total=10), min_documents=300)
    )

    assert is_duplicate is False


async def test_each_message_is_counted_once() -> None:
    frequencies = InMemoryStemFrequencyIndex()
    dedup = _weighted(frequencies)
    message = _from_source(_BACKDOOR, "whackdoor", "1")

    await dedup.check(message)
    await dedup.check(message)  # повторная проверка после паузы на FloodWait

    assert frequencies.total == 1
    assert frequencies.counts["хуанг"] == 1
    assert "бэкдо" not in frequencies.counts  # подпись канала не учитывается


async def test_frequencies_are_loaded_from_the_index() -> None:
    frequencies = InMemoryStemFrequencyIndex(total=1000)
    dedup = _weighted(frequencies)

    await dedup.check(_from_source(_BACKDOOR, "whackdoor", "1"))

    assert frequencies.total == 1001


def test_weighted_rule_needs_frequency_index() -> None:
    with pytest.raises(ValueError, match="frequency_index"):
        ParaphraseDeduplicator(InMemoryLemmaIndex(), weighted_min_jaccard=0.15)


@pytest.mark.parametrize("value", [0.0, 1.5])
def test_weighted_threshold_is_validated(value: float) -> None:
    with pytest.raises(ValueError, match="weighted_min_jaccard"):
        ParaphraseDeduplicator(
            InMemoryLemmaIndex(),
            frequency_index=InMemoryStemFrequencyIndex(),
            weighted_min_jaccard=value,
        )


async def test_channel_signature_is_ignored() -> None:
    # Без подписей «👍 Бэкдор» и «🌒 1337» — те же основы, что и с ними.
    index = InMemoryLemmaIndex()
    dedup = ParaphraseDeduplicator(index)
    await dedup.remember(_from_source(_BACKDOOR, "whackdoor", "1"))

    (saved,) = await index.find_recent(datetime.now(UTC) - timedelta(hours=1))

    assert "бэкдор" not in saved
    assert "хуанг" in {lemma[:5] for lemma in saved}
