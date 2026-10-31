"""Качество дедупликатора пересказов на корпусе tests/support/dedup_corpus.py.

Гоняет НАСТОЯЩИЙ ParaphraseDeduplicator по всем парам текстов: одно сообщение
«уже опубликовано», второе проверяется. Варианты одного события — дубли, тексты
разных событий — нет. Корпус синтетический, поэтому тест защищает от грубых
регрессий при подборе порогов, но не заменяет проверку на реальной ленте.
"""

from __future__ import annotations

import functools
from datetime import datetime
from itertools import combinations

import pytest

import news_aggregator.dedup.paraphrase_deduplicator as paraphrase_module
from news_aggregator.core.lemmatize import extract_lemma_set
from news_aggregator.core.models import ProcessedMessage, RawMessage
from news_aggregator.core.text_utils import compute_text_hash, normalize_text
from news_aggregator.dedup.paraphrase_deduplicator import ParaphraseDeduplicator
from tests.support.dedup_corpus import KNOWN_HARD_NEGATIVE, STORIES
from tests.support.in_memory_lemma_index import InMemoryLemmaIndex

# Ключ — (событие, вариант): H — заголовок, L — полный текст, R — пересказ.
Key = tuple[str, str]

# Единственное известное ложное срабатывание: решение ЦБ и новость про инфляцию —
# темы родственные (обе про замедление роста цен), лексикой их не разделить.
KNOWN_FALSE_POSITIVES = {frozenset({("cb_rate_hold_16", "L"), ("inflation", "R")})}


@pytest.fixture(autouse=True)
def _cache_lemmatization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Корпус гоняется по тысячам пар: кэш лемм ускоряет тест с ~30 до ~2 секунд."""
    monkeypatch.setattr(
        paraphrase_module, "extract_lemma_set", functools.lru_cache(maxsize=None)(extract_lemma_set)
    )


def _message(text: str, ident: str) -> ProcessedMessage:
    normalized = normalize_text(text)
    raw = RawMessage(
        source_id="src",
        external_id=ident,
        text=text,
        posted_at=datetime.now(),
        fetched_at=datetime.now(),
    )
    return ProcessedMessage(
        raw=raw, normalized_text=normalized, text_hash=compute_text_hash(normalized)
    )


def _all_messages() -> dict[Key, ProcessedMessage]:
    messages: dict[Key, ProcessedMessage] = {}
    for sid, headline, long_text, rewrite in [*STORIES, KNOWN_HARD_NEGATIVE]:
        for variant, text in zip("HLR", (headline, long_text, rewrite), strict=True):
            messages[(sid, variant)] = _message(text, f"{sid}-{variant}")
    return messages


async def _is_duplicate(a: ProcessedMessage, b: ProcessedMessage, **params: object) -> bool:
    dedup = ParaphraseDeduplicator(InMemoryLemmaIndex(), **params)  # type: ignore[arg-type]
    await dedup.remember(a)
    return (await dedup.check(b)).is_duplicate


async def _evaluate(**params: object) -> tuple[int, int, set[frozenset[Key]]]:
    """Возвращает (найдено дублей, всего пар-дублей, множество ложных пар)."""
    messages = {k: v for k, v in _all_messages().items() if k[0] != KNOWN_HARD_NEGATIVE[0]}
    found = total = 0
    false_positives: set[frozenset[Key]] = set()
    for (k1, m1), (k2, m2) in combinations(messages.items(), 2):
        flagged = await _is_duplicate(m1, m2, **params)
        if k1[0] == k2[0]:
            total += 1
            found += flagged
        elif flagged:
            false_positives.add(frozenset({k1, k2}))
    return found, total, false_positives


_OLD_BEHAVIOUR = {"stem_length": None, "min_shared_lemmas": 1, "number_veto": False}


async def test_default_settings_find_most_rewrites_without_new_false_positives() -> None:
    found, total, false_positives = await _evaluate()

    assert total == 69
    assert found / total >= 0.8, f"найдено лишь {found} из {total}"
    assert false_positives <= KNOWN_FALSE_POSITIVES, (
        f"новые ложные срабатывания: {false_positives - KNOWN_FALSE_POSITIVES}"
    )


async def test_default_settings_beat_the_previous_ones_on_both_axes() -> None:
    new_found, _, new_fp = await _evaluate()
    old_found, _, old_fp = await _evaluate(**_OLD_BEHAVIOUR)

    assert new_found > old_found
    assert len(new_fp) < len(old_fp)


# Пары, которые старое правило пропускало, а текущее ловит (проверены на настоящем коде).
@pytest.mark.parametrize(
    ("story", "variants"),
    [
        ("hockey", ("H", "R")),  # «Канада» / «канадцы», «хоккей» / «хоккейный»
        ("crash_m4", ("H", "R")),
        ("law_ads", ("H", "L")),
        ("weather", ("L", "R")),
    ],
)
async def test_rewrites_the_old_rule_missed_are_now_matched(
    story: str, variants: tuple[str, str]
) -> None:
    messages = _all_messages()
    a, b = (messages[(story, v)] for v in variants)

    assert await _is_duplicate(a, b) is True
    assert await _is_duplicate(a, b, **_OLD_BEHAVIOUR) is False


async def test_nothing_caught_by_the_old_rule_is_lost() -> None:
    """Улучшение чистое: каждая пара-дубль, найденная раньше, находится и теперь."""
    messages = {k: v for k, v in _all_messages().items() if k[0] != KNOWN_HARD_NEGATIVE[0]}
    for (k1, m1), (k2, m2) in combinations(messages.items(), 2):
        if k1[0] != k2[0]:
            continue
        if await _is_duplicate(m1, m2, **_OLD_BEHAVIOUR):
            assert await _is_duplicate(m1, m2) is True, (k1, k2)


# Ложные срабатывания старого правила: общие слова («ключевая ставка», «обыграл Россию»)
# на коротких текстах плюс совпадающие по теме, но разные по цифрам события.
@pytest.mark.parametrize(
    ("first", "second"),
    [
        (("cb_rate_18", "H"), ("cb_rate_hold_16", "H")),
        (("cb_rate_18", "L"), ("cb_rate_hold_16", "L")),
        (("ruble_rate", "L"), ("bitcoin", "L")),
        (("hockey", "H"), ("football", "H")),
    ],
)
async def test_old_false_positives_are_gone(first: Key, second: Key) -> None:
    messages = _all_messages()
    a, b = messages[first], messages[second]

    assert await _is_duplicate(a, b, **_OLD_BEHAVIOUR) is True  # так было
    assert await _is_duplicate(a, b) is False


async def test_same_topic_with_different_figures_is_not_a_duplicate() -> None:
    """Ставка 19% и ставка 18% — разные решения ЦБ, хотя лексика почти одинакова."""
    messages = _all_messages()

    for variant in "HLR":
        a = messages[("cb_rate_18", variant)]
        b = messages[("cb_rate_19", variant)]
        assert await _is_duplicate(a, b) is False, variant
        assert await _is_duplicate(a, b, **_OLD_BEHAVIOUR) is True, variant  # было ошибкой


async def test_pairs_with_too_few_shared_stems_stay_unmatched() -> None:
    """Предел подхода: у «Курс биткоина обновил…» и «Биткоин взлетел…» одна общая основа."""
    messages = _all_messages()

    assert await _is_duplicate(messages[("bitcoin", "H")], messages[("bitcoin", "R")]) is False
