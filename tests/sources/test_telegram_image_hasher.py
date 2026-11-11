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


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (320, 200), (200, 30, 30)).save(buffer, "JPEG")
    return buffer.getvalue()


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
    def __init__(self, error: Exception | None = None) -> None:
        self.downloads: list[tuple[Any, Any]] = []
        self._error = error

    def is_connected(self) -> bool:
        return True

    async def download_media(self, media: Any, file: Any = None, thumb: Any = None) -> bytes:
        self.downloads.append((media, thumb))
        if self._error is not None:
            raise self._error
        return _jpeg()


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


async def test_album_uses_its_first_photo() -> None:
    client = _Client()
    document = types.Document(
        id=5,
        access_hash=1,
        file_reference=b"",
        date=datetime.now(UTC),
        mime_type="video/mp4",
        size=1,
        dc_id=2,
        attributes=[],
    )
    photo = _photo(7)

    await _hasher(client).hash(MediaAttachment(kind="album", native_ref=[document, photo]))

    assert client.downloads[0][0] is photo


@pytest.mark.parametrize("kind", ["link_preview", "document"])
async def test_link_previews_and_documents_are_not_downloaded(kind: str) -> None:
    client = _Client()

    result = await _hasher(client).hash(MediaAttachment(kind=kind, native_ref=_photo(1)))

    assert result is None
    assert client.downloads == []


async def test_flood_wait_becomes_rate_limit() -> None:
    error = FloodWaitError(request=None, capture=120)

    with pytest.raises(RateLimitedError):
        await _hasher(_Client(error)).hash(MediaAttachment(kind="photo", native_ref=_photo(1)))


async def test_download_failure_does_not_block_the_post() -> None:
    client = _Client(ConnectionError("нет сети"))

    result = await _hasher(client).hash(MediaAttachment(kind="photo", native_ref=_photo(1)))

    assert result is None
