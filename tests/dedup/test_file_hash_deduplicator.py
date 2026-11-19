from datetime import UTC, datetime

import pytest

from news_aggregator.core.interfaces import IFileHasher
from news_aggregator.core.models import MediaAttachment, ProcessedMessage, RawMessage
from news_aggregator.core.text_utils import compute_text_hash, normalize_text
from news_aggregator.dedup.file_hash_deduplicator import FileHashDeduplicator
from tests.support.in_memory_file_hash_index import InMemoryFileHashIndex

_FILE_HASH_A = "a" * 64
_FILE_HASH_B = "b" * 64
_EVENT_A = "Суд отложил рассмотрение дела о шпионаже"
_EVENT_B = "Судья отложил рассмотрение дела о шпионаже"
_OTHER_EVENT = "Вышел новый сезон сериала про космос"


class _FakeFileHasher(IFileHasher):
    """Хэш берётся из fingerprint вложения: так тест задаёт «файл»."""

    def __init__(self) -> None:
        self.calls = 0

    async def hash(self, media) -> str | None:
        self.calls += 1
        return media.fingerprint or None


def _message(
    source_id: str, external_id: str, text: str, file_hash: str | None = _FILE_HASH_A
) -> ProcessedMessage:
    now = datetime.now(UTC)
    media = (
        MediaAttachment(kind="document", native_ref=object(), fingerprint=file_hash)
        if file_hash is not None
        else None
    )
    normalized = normalize_text(text)
    return ProcessedMessage(
        raw=RawMessage(
            source_id=source_id,
            external_id=external_id,
            text=text,
            posted_at=now,
            fetched_at=now,
            media=media,
        ),
        normalized_text=normalized,
        text_hash=compute_text_hash(normalized),
    )


def _dedup(hasher: IFileHasher | None = None, **kwargs: object) -> FileHashDeduplicator:
    return FileHashDeduplicator(
        InMemoryFileHashIndex(),
        _FakeFileHasher() if hasher is None else hasher,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_same_file_from_other_channel_with_rewritten_text_is_duplicate() -> None:
    """Регресс: один и тот же файл, другой канал, другая подпись — дубль."""
    dedup = _dedup()
    await dedup.remember(_message("concertzaal", "19609", _EVENT_A, _FILE_HASH_A))

    result = await dedup.check(_message("media1337", "29210", _EVENT_B, _FILE_HASH_A))

    assert result.is_duplicate
    assert "concertzaal" in result.reason


async def test_same_source_reusing_its_file_is_not_duplicate() -> None:
    dedup = _dedup()
    await dedup.remember(_message("concertzaal", "19609", _EVENT_A, _FILE_HASH_A))

    result = await dedup.check(_message("concertzaal", "19610", _EVENT_B, _FILE_HASH_A))

    assert not result.is_duplicate


async def test_same_file_but_texts_about_different_events_is_not_duplicate() -> None:
    """Стоковая картинка/файл у разных историй: контент совпал, тексты — нет."""
    dedup = _dedup()
    await dedup.remember(_message("tech", "1", _EVENT_A, _FILE_HASH_A))

    result = await dedup.check(_message("news", "2", _OTHER_EVENT, _FILE_HASH_A))

    assert not result.is_duplicate
    assert "общих основ" in result.reason


async def test_different_files_are_not_duplicate() -> None:
    dedup = _dedup()
    await dedup.remember(_message("concertzaal", "19609", _EVENT_A, _FILE_HASH_A))

    result = await dedup.check(_message("media1337", "29210", _EVENT_B, _FILE_HASH_B))

    assert not result.is_duplicate


async def test_post_without_file_is_skipped_without_download() -> None:
    hasher = _FakeFileHasher()
    dedup = _dedup(hasher)

    result = await dedup.check(_message("media1337", "29210", _EVENT_B, None))

    assert not result.is_duplicate
    assert hasher.calls == 0


async def test_file_is_hashed_once_per_message() -> None:
    hasher = _FakeFileHasher()
    dedup = _dedup(hasher)
    message = _message("concertzaal", "19609", _EVENT_A, _FILE_HASH_A)

    await dedup.check(message)
    await dedup.remember(message)

    assert hasher.calls == 1


async def test_without_hasher_layer_is_inert() -> None:
    dedup = FileHashDeduplicator(InMemoryFileHashIndex(), None)
    await dedup.remember(_message("concertzaal", "19609", _EVENT_A, _FILE_HASH_A))

    result = await dedup.check(_message("media1337", "29210", _EVENT_B, _FILE_HASH_A))

    assert not result.is_duplicate


@pytest.mark.parametrize(
    "kwargs",
    [{"lookback_hours": 0}, {"min_shared_stems": 0}, {"min_text_stems": 0}],
)
def test_invalid_params_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _dedup(**kwargs)
