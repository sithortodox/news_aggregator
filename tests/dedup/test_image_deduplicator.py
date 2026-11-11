from datetime import UTC, datetime

import pytest

from news_aggregator.core.interfaces import IImageHasher
from news_aggregator.core.models import MediaAttachment, ProcessedMessage, RawMessage
from news_aggregator.core.text_utils import compute_text_hash, normalize_text
from news_aggregator.dedup.image_deduplicator import ImageDeduplicator
from tests.support.in_memory_image_hash_index import InMemoryImageHashIndex

_PICTURE = (1 << 255) | 0xABCDEF
_DUROV_A = (
    "ЖИЗА: к Дурову не засылают шпионок, потому что он не любит азиаток, и это "
    "несправедливо. С Францией бате всё и так понятно — там девушек вообще не любят."
)
_DUROV_B = "Дуров поделился, что ему не присылают шпионок. Признан экстремистом и террористом"


class _Hasher(IImageHasher):
    """Хэш берётся из fingerprint вложения: так тест задаёт «картинку»."""

    def __init__(self) -> None:
        self.calls = 0

    async def hash(self, media: MediaAttachment) -> int | None:
        self.calls += 1
        return int(media.fingerprint) if media.fingerprint else None


def _message(
    source_id: str, external_id: str, text: str, picture: int | None = _PICTURE
) -> ProcessedMessage:
    now = datetime.now(UTC)
    media = (
        MediaAttachment(kind="photo", native_ref=object(), fingerprint=str(picture))
        if picture is not None
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


def _dedup(hasher: IImageHasher | None = None, **kwargs: object) -> ImageDeduplicator:
    return ImageDeduplicator(
        InMemoryImageHashIndex(),
        _Hasher() if hasher is None else hasher,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_same_picture_with_different_captions_from_other_channel_is_duplicate() -> None:
    """Регресс: один скриншот поста Дурова в whackdoor и media1337, подписи разные."""
    dedup = _dedup()
    await dedup.remember(_message("whackdoor", "32871", _DUROV_A))

    result = await dedup.check(_message("media1337", "29118", _DUROV_B, _PICTURE ^ 0b111))

    assert result.is_duplicate
    assert "whackdoor" in result.reason


async def test_same_channel_reusing_its_picture_is_not_duplicate() -> None:
    dedup = _dedup()
    await dedup.remember(_message("whackdoor", "1", _DUROV_A))

    result = await dedup.check(_message("whackdoor", "2", _DUROV_B))

    assert not result.is_duplicate


async def test_distant_picture_is_not_duplicate() -> None:
    dedup = _dedup()
    await dedup.remember(_message("whackdoor", "1", _DUROV_A))

    result = await dedup.check(_message("media1337", "2", _DUROV_B, _PICTURE ^ ((1 << 21) - 1)))

    assert not result.is_duplicate
    assert "21" in result.reason


async def test_same_stock_picture_for_different_stories_is_not_duplicate() -> None:
    dedup = _dedup()
    await dedup.remember(
        _message("tech", "1", "Дуров рассказал о новых функциях мессенджера для бизнеса")
    )

    result = await dedup.check(
        _message("news", "2", "Французский суд отложил заседание по делу основателя платформы")
    )

    assert not result.is_duplicate
    assert "общих основ" in result.reason


async def test_short_caption_is_decided_by_picture_alone() -> None:
    # Мем с подписью канала: текста для сравнения нет, решает картинка.
    dedup = _dedup()
    await dedup.remember(_message("whackdoor", "1", "Думайте 👍 Бэкдор"))

    result = await dedup.check(_message("media1337", "2", "🌒 1337"))

    assert result.is_duplicate


async def test_post_without_picture_is_skipped_without_download() -> None:
    hasher = _Hasher()
    dedup = _dedup(hasher)

    result = await dedup.check(_message("media1337", "2", _DUROV_B, picture=None))

    assert not result.is_duplicate
    assert hasher.calls == 0


async def test_picture_is_downloaded_once_per_message() -> None:
    hasher = _Hasher()
    dedup = _dedup(hasher)
    message = _message("whackdoor", "1", _DUROV_A)

    await dedup.check(message)
    await dedup.remember(message)

    assert hasher.calls == 1


async def test_without_hasher_layer_is_inert() -> None:
    dedup = ImageDeduplicator(InMemoryImageHashIndex(), None)
    await dedup.remember(_message("whackdoor", "1", _DUROV_A))

    result = await dedup.check(_message("media1337", "2", _DUROV_B))

    assert not result.is_duplicate


@pytest.mark.parametrize(
    "kwargs",
    [{"max_distance": -1}, {"max_distance": 256}, {"lookback_hours": 0}, {"min_shared_stems": 0}],
)
def test_invalid_params_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _dedup(**kwargs)
