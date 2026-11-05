from datetime import datetime
from types import SimpleNamespace

import pytest

from news_aggregator.core.models import ProcessedMessage, RawMessage
from news_aggregator.core.text_utils import compute_text_hash, normalize_text
from news_aggregator.filters.ad_filter import AdFilter


def _make_message(text: str) -> ProcessedMessage:
    raw = RawMessage(
        source_id="src1",
        external_id="1",
        text=text,
        posted_at=datetime.now(),
        fetched_at=datetime.now(),
    )
    normalized = normalize_text(text)
    return ProcessedMessage(
        raw=raw,
        normalized_text=normalized,
        text_hash=compute_text_hash(normalized),
    )


def test_drops_message_with_default_ad_keyword() -> None:
    filt = AdFilter()
    should_drop, reason = filt.should_drop(_make_message("Такая скидка только сегодня!"))
    assert should_drop is True
    assert reason is not None


def test_keeps_message_without_ad_keywords() -> None:
    filt = AdFilter()
    should_drop, _ = filt.should_drop(_make_message("Сегодня прошла важная встреча"))
    assert should_drop is False


def test_custom_keywords_are_case_and_punctuation_insensitive() -> None:
    filt = AdFilter(keywords=["ПРОМОКОД!!!"])
    should_drop, _ = filt.should_drop(_make_message("Вот твой промокод на скидку"))
    assert should_drop is True


def test_empty_keywords_never_drops() -> None:
    filt = AdFilter(keywords=[])
    should_drop, _ = filt.should_drop(_make_message("реклама промокод скидка"))
    assert should_drop is False


def test_name_property() -> None:
    assert AdFilter().name == "ad_filter"


# ---------------------------------------------------- маркировка рекламы erid


def _msg(text: str, entities: tuple[object, ...] = ()) -> ProcessedMessage:
    normalized = normalize_text(text)
    raw = RawMessage(
        source_id="src1",
        external_id="1",
        text=text,
        posted_at=datetime.now(),
        fetched_at=datetime.now(),
        formatting_entities=entities,
    )
    return ProcessedMessage(
        raw=raw, normalized_text=normalized, text_hash=compute_text_hash(normalized)
    )


@pytest.mark.parametrize(
    "text",
    [
        "Курс по программированию. Реклама. ООО «Школа», ИНН 7701234567. erid: 2VtzqwEJbQ8Hk2",
        "Закажите сегодня ERID=2VtzqwEJbQ8Hk2",
        "Подробности https://shop.example.com/x?erid=2VtzqwEJbQ8Hk2&a=1",
        "Erid 2VtzqwEJbQ8Hk2",
        "erid:2VtzqwEJbQ8",
    ],
)
def test_erid_marking_marks_post_as_advertising(text: str) -> None:
    drop, reason = AdFilter(keywords=[]).should_drop(_msg(text))

    assert drop is True
    assert reason is not None
    assert "маркировка" in reason


@pytest.mark.parametrize(
    "text",
    [
        "Отель Meridian 2VtzqwEJbQ8 открылся в центре города",  # «erid» внутри слова
        "Новая superidentity платформа для банков",  # «erid» в середине слова + 6 символов
        "Параметр erid обязателен для рекламы",  # слово без токена
        "erid: abc",  # токен слишком короткий
        "Обычная новость про экономику и ставки",
    ],
)
def test_erid_lookalikes_are_not_advertising(text: str) -> None:
    assert AdFilter(keywords=[]).should_drop(_msg(text))[0] is False


def test_erid_in_hyperlink_url_is_detected() -> None:
    entity = SimpleNamespace(url="https://shop.example.com/x?erid=2VtzqwEJbQ8Hk2")

    assert AdFilter(keywords=[]).should_drop(_msg("Подробнее о скидках", (entity,)))[0] is True


def test_erid_check_is_on_by_default_and_can_be_disabled() -> None:
    text = "Акция erid: 2VtzqwEJbQ8Hk2"

    assert AdFilter(keywords=[]).should_drop(_msg(text))[0] is True
    assert AdFilter(keywords=[], patterns=[]).should_drop(_msg(text))[0] is False


def test_custom_patterns_replace_the_default_ones() -> None:
    dedup = AdFilter(keywords=[], patterns=[r"партн[её]рский\s+материал"])

    assert dedup.should_drop(_msg("Партнёрский материал: новый сервис"))[0] is True
    assert dedup.should_drop(_msg("erid: 2VtzqwEJbQ8Hk2"))[0] is False


def test_invalid_pattern_is_rejected_at_creation() -> None:
    with pytest.raises(ValueError):
        AdFilter(patterns=["(не закрыта"])
