"""IImageHasher для Telegram: скачивает превью фото или обложку видео и считает dHash.

Скачивается не оригинал, а превью размера «m» (до 320 точек, десятки КБ):
для dHash этого с запасом, а запрос лёгкий. У видео и GIF хэшируется обложка,
которую Telegram делает из кадра видео: когда два канала по отдельности
загружают один ролик, обложки почти совпадают. У альбома берётся первое фото
или видео. Остальные файлы (документы, стикеры, голосовые) не хэшируются.
Превью ссылок ридер помечает видом «link_preview» и сюда они не попадают:
у статей одного сайта часто одна и та же картинка.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl import types

from news_aggregator.core.errors import RateLimitedError
from news_aggregator.core.image_hash import informative_dhash
from news_aggregator.core.interfaces import IImageHasher
from news_aggregator.core.models import MediaAttachment
from news_aggregator.sources.telegram_client import ensure_connected

logger = logging.getLogger(__name__)

# Размеры фото в порядке предпочтения: «m» — 320 точек, «x» — 800, «s» — 100.
_THUMB_TYPES = ("m", "x", "s")


def _is_video(document: Any) -> bool:
    """Видео или GIF, но не видеостикер и не кружок без обложки."""
    if not isinstance(document, types.Document):
        return False
    attributes = document.attributes or []
    if any(isinstance(a, types.DocumentAttributeSticker) for a in attributes):
        return False
    return any(
        isinstance(a, types.DocumentAttributeVideo | types.DocumentAttributeAnimated)
        for a in attributes
    )


def _is_visual(ref: Any) -> bool:
    return isinstance(ref, types.Photo) or _is_video(ref)


def _first_visual(media: MediaAttachment) -> Any | None:
    if media.kind == "photo":
        return media.native_ref if isinstance(media.native_ref, types.Photo) else None
    if media.kind == "document":
        return media.native_ref if _is_video(media.native_ref) else None
    if media.kind == "album" and isinstance(media.native_ref, list):
        return next((ref for ref in media.native_ref if _is_visual(ref)), None)
    return None


def _thumb_type(ref: Any) -> str | None:
    sizes = ref.sizes if isinstance(ref, types.Photo) else ref.thumbs
    available = {getattr(size, "type", None) for size in sizes or ()}
    return next((kind for kind in _THUMB_TYPES if kind in available), None)


class TelegramImageHasher(IImageHasher):
    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    async def hash(self, media: MediaAttachment) -> int | None:
        ref = _first_visual(media)
        if ref is None:
            return None
        thumb = _thumb_type(ref)
        if thumb is None:
            return None
        try:
            await ensure_connected(self._client)
            data = await self._client.download_media(ref, file=bytes, thumb=thumb)
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
            return await asyncio.to_thread(informative_dhash, data)
        except Exception:
            logger.warning("Не удалось посчитать хэш картинки", exc_info=True)
            return None
