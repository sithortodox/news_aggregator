import io
from datetime import UTC, datetime
from typing import Any

import pytest
from PIL import Image
from telethon.errors import FloodWaitError
from telethon.tl import types

from news_aggregator.core.errors import RateLimitedError
from news_aggregator.core.image_hash import dhash
from news_aggregator.core.models import MediaAttachment
from news_aggregator.sources.telegram_image_hasher import TelegramImageHasher


def _jpeg(color: tuple[int, int, int] | None = None) -> bytes:
    """Градиент (есть что сравнивать) или однотонная заливка, если задан цвет."""
    if color is not None:
        image = Image.new("RGB", (320, 200), color)
    else:
        image = Image.linear_gradient("L").resize((320, 200)).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    return buffer.getvalue()


def _document(
    document_id: int, attributes: list[Any], thumb_types: tuple[str, ...] = ("m",)
) -> types.Document:
    return types.Document(
        id=document_id,
        access_hash=1,
        file_reference=b"",
        date=datetime.now(UTC),
        mime_type="video/mp4",
        size=1,
        dc_id=2,
        attributes=attributes,
        thumbs=[types.PhotoSize(type=t, w=320, h=180, size=1000) for t in thumb_types],
    )


def _video_attribute() -> types.DocumentAttributeVideo:
    return types.DocumentAttributeVideo(duration=42, w=1280, h=720)


def _photo(photo_id: int, size_types: tuple[str, ...] = ("s", "m", "x")) -> types.Photo:
    return types.Photo(
        id=photo_id,
        access_hash=1,
        file_reference=b"",
        date=datetime.now(UTC),
        sizes=[types.PhotoSize(type=t, w=100, h=100, size=1000) for t in size_types],
        dc_id=2,
    )


class _Client:
    def __init__(self, error: Exception | None = None, data: bytes | None = None) -> None:
        self.downloads: list[tuple[Any, Any]] = []
        self._error = error
        self._data = data

    def is_connected(self) -> bool:
        return True

    async def download_media(self, media: Any, file: Any = None, thumb: Any = None) -> bytes:
        self.downloads.append((media, thumb))
        if self._error is not None:
            raise self._error
        return self._data if self._data is not None else _jpeg()


def _hasher(client: _Client) -> TelegramImageHasher:
    return TelegramImageHasher(client)  # type: ignore[arg-type]


async def test_photo_is_hashed_from_medium_thumbnail() -> None:
    client = _Client()
    photo = _photo(1)

    result = await _hasher(client).hash(MediaAttachment(kind="photo", native_ref=photo))

    assert result == dhash(_jpeg())
    assert client.downloads == [(photo, "m")]


async def test_falls_back_to_other_size_when_medium_is_missing() -> None:
    client = _Client()

    await _hasher(client).hash(MediaAttachment(kind="photo", native_ref=_photo(1, ("s", "x"))))

    assert client.downloads[0][1] == "x"


async def test_album_skips_files_and_uses_its_first_photo() -> None:
    client = _Client()
    pdf = _document(5, [types.DocumentAttributeFilename(file_name="a.pdf")])
    photo = _photo(7)

    await _hasher(client).hash(MediaAttachment(kind="album", native_ref=[pdf, photo]))

    assert client.downloads[0][0] is photo


async def test_album_starting_with_video_uses_its_cover() -> None:
    client = _Client()
    video = _document(5, [_video_attribute()])

    await _hasher(client).hash(MediaAttachment(kind="album", native_ref=[video, _photo(7)]))

    assert client.downloads == [(video, "m")]


@pytest.mark.parametrize(
    "attributes",
    [[_video_attribute()], [types.DocumentAttributeAnimated()]],
    ids=["video", "gif"],
)
async def test_video_is_hashed_from_its_cover(attributes: list[Any]) -> None:
    # Регрессия: exploitex/37809 и media1337/29125 — один ролик, загруженный
    # двумя каналами; раньше видео не хэшировались и дубль вышел дважды.
    client = _Client()
    video = _document(5, attributes)

    result = await _hasher(client).hash(MediaAttachment(kind="document", native_ref=video))

    assert result == dhash(_jpeg())
    assert client.downloads == [(video, "m")]


@pytest.mark.parametrize(
    "attributes",
    [
        [types.DocumentAttributeFilename(file_name="report.pdf")],
        [types.DocumentAttributeAudio(duration=5, voice=True)],
        [
            _video_attribute(),
            types.DocumentAttributeSticker(alt="", stickerset=types.InputStickerSetEmpty()),
        ],
    ],
    ids=["file", "voice", "video-sticker"],
)
async def test_other_documents_are_not_downloaded(attributes: list[Any]) -> None:
    client = _Client()

    result = await _hasher(client).hash(
        MediaAttachment(kind="document", native_ref=_document(5, attributes))
    )

    assert result is None
    assert client.downloads == []


async def test_video_without_cover_is_skipped() -> None:
    client = _Client()
    video = _document(5, [_video_attribute()], thumb_types=())

    result = await _hasher(client).hash(MediaAttachment(kind="document", native_ref=video))

    assert result is None
    assert client.downloads == []


async def test_link_previews_are_not_downloaded() -> None:
    client = _Client()

    result = await _hasher(client).hash(MediaAttachment(kind="link_preview", native_ref=_photo(1)))

    assert result is None
    assert client.downloads == []


async def test_blank_picture_is_not_compared() -> None:
    # Чёрный первый кадр есть у многих разных роликов: по нему всё совпало бы.
    client = _Client(data=_jpeg((0, 0, 0)))

    result = await _hasher(client).hash(
        MediaAttachment(kind="document", native_ref=_document(5, [_video_attribute()]))
    )

    assert result is None


async def test_flood_wait_becomes_rate_limit() -> None:
    error = FloodWaitError(request=None, capture=120)

    with pytest.raises(RateLimitedError):
        await _hasher(_Client(error)).hash(MediaAttachment(kind="photo", native_ref=_photo(1)))


async def test_download_failure_does_not_block_the_post() -> None:
    client = _Client(ConnectionError("нет сети"))

    result = await _hasher(client).hash(MediaAttachment(kind="photo", native_ref=_photo(1)))

    assert result is None
