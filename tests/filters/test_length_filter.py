from dataclasses import replace
from datetime import datetime

import pytest

from news_aggregator.core.models import MediaAttachment, ProcessedMessage, RawMessage
from news_aggregator.core.text_utils import compute_text_hash, normalize_text
from news_aggregator.filters.length_filter import LengthFilter


def _make_message_with_media(text: str) -> ProcessedMessage:
    plain = _make_message(text)
    media = MediaAttachment(kind="photo", native_ref=object())
    return replace(plain, raw=replace(plain.raw, media=media))


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


def test_drops_empty_text() -> None:
    filt = LengthFilter(min_length=10)
    should_drop, reason = filt.should_drop(_make_message(""))
    assert should_drop is True
    assert reason is not None


def test_drops_short_text() -> None:
    filt = LengthFilter(min_length=20)
    should_drop, reason = filt.should_drop(_make_message("коротко"))
    assert should_drop is True


def test_keeps_long_enough_text() -> None:
    filt = LengthFilter(min_length=10)
    should_drop, reason = filt.should_drop(_make_message("это достаточно длинный текст новости"))
    assert should_drop is False
    assert reason is None


def test_negative_min_length_raises() -> None:
    with pytest.raises(ValueError):
        LengthFilter(min_length=-1)


def test_name_property() -> None:
    assert LengthFilter().name == "length_filter"


def test_keeps_media_without_text() -> None:
    assert LengthFilter(min_length=10).should_drop(_make_message_with_media("")) == (False, None)


def test_media_without_text_can_be_dropped_by_config() -> None:
    filt = LengthFilter(min_length=10, allow_media_only=False)
    should_drop, _ = filt.should_drop(_make_message_with_media(""))
    assert should_drop is True


def test_short_caption_on_media_is_still_dropped() -> None:
    should_drop, _ = LengthFilter(min_length=20).should_drop(_make_message_with_media("Фото дня"))
    assert should_drop is True
