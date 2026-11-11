"""IImageHasher для Telegram: скачивает превью фото и считает dHash.

Скачивается не оригинал, а превью размера «m» (до 320 точек, десятки КБ):
для dHash этого с запасом, а запрос лёгкий. Хэшируются только фото и первое
фото альбома. Превью ссылок ридер помечает видом «link_preview» и сюда они не
попадают: у статей одного сайта часто одна и та же картинка.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl import types

from news_aggregator.core.errors import RateLimitedError
from news_aggregator.core.image_hash import dhash
from news_aggregator.core.interfaces import IImageHasher
from news_aggregator.core.models import MediaAttachment
from news_aggregator.sources.telegram_client import ensure_connected

logger = logging.getLogger(__name__)

# Размеры фото в порядке предпочтения: «m» — 320 точек, «x» — 800, «s» — 100.
_THUMB_TYPES = ("m", "x", "s")


def _first_photo(media: MediaAttachment) -> Any | None:
    if media.kind == "photo":
        return media.native_ref
    if media.kind == "album" and isinstance(media.native_ref, list):
        return next((ref for ref in media.native_ref if isinstance(ref, types.Photo)), None)
    return None


def _thumb_type(photo: Any) -> str | None:
    available = {getattr(size, "type", None) for size in getattr(photo, "sizes", ())}
    return next((kind for kind in _THUMB_TYPES if kind in available), None)


class TelegramImageHasher(IImageHasher):
    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    async def hash(self, media: MediaAttachment) -> int | None:
        photo = _first_photo(media)
        if photo is None:
            return None
        thumb = _thumb_type(photo)
        if thumb is None:
            return None
        try:
            await ensure_connected(self._client)
            data = await self._client.download_media(photo, file=bytes, thumb=thumb)
        except FloodWaitError as exc:
            raise RateLimitedError(
                exc.seconds, f"FloodWait {exc.seconds} с при скачивании картинки"
            ) from exc
        except Exception:
            # Без картинки сообщение проверят остальные слои; терять его из-за
            # сбоя скачивания нельзя.
            logger.warning("Не удалось скачать картинку для сравнения", exc_info=True)
            return None
        if not isinstance(data, bytes) or not data:
            return None
        try:
            return await asyncio.to_thread(dhash, data)
        except Exception:
            logger.warning("Не удалось посчитать хэш картинки", exc_info=True)
            return None
