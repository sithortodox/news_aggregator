"""IFileHasher для Telegram: скачивает файл/медиа и считает SHA-256 контента.

Отличается от TelegramImageHasher тем, что хэшируется полный контент файла
(не перцептивный dHash превью): так один и тот же файл, переупакованный в
другом канале с другим file_id, получит тот же SHA-256. Сюда попадают и
документы (PDF, флаеры, афиши), и видео — раньше документы сравнивались
только по обложке видео, а нуловые файлы вообще игнорировались.

Скачивание и SHA-256 — одна операция: считаем по содержимому, поэтому
переименование/перекодировка файла не даст ложных срабатываний.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl import types

from news_aggregator.core.errors import RateLimitedError
from news_aggregator.core.file_hash import compute_file_hash
from news_aggregator.core.interfaces import IFileHasher
from news_aggregator.core.models import MediaAttachment
from news_aggregator.sources.telegram_client import ensure_connected

logger = logging.getLogger(__name__)


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


def _ref_for(media: MediaAttachment) -> Any | None:
    """Какой файл скачивать: фото, документ или первое фото/видео альбома."""
    if media.kind == "album" and isinstance(media.native_ref, list):
        for ref in media.native_ref:
            if isinstance(ref, types.Photo) or _is_video(ref):
                return ref
        return None
    if media.kind == "photo":
        return media.native_ref if isinstance(media.native_ref, types.Photo) else None
    if media.kind == "document":
        return media.native_ref if media.native_ref is not None else None
    return None


class TelegramFileHasher(IFileHasher):
    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    async def hash(self, media: MediaAttachment) -> str | None:
        ref = _ref_for(media)
        if ref is None:
            return None
        try:
            await ensure_connected(self._client)
            data = await self._client.download_media(ref, file=bytes)
        except FloodWaitError as exc:
            raise RateLimitedError(
                exc.seconds, f"FloodWait {exc.seconds} с при скачивании файла"
            ) from exc
        except Exception:
            # Без файла сообщение проверят остальные слои; терять его из-за
            # сбоя скачивания нельзя.
            logger.warning("Не удалось скачать файл для сравнения", exc_info=True)
            return None
        if not isinstance(data, bytes) or not data:
            return None
        try:
            return await asyncio.to_thread(compute_file_hash, data)
        except Exception:
            logger.warning("Не удалось посчитать хэш файла", exc_info=True)
            return None
