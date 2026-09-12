from datetime import datetime

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
