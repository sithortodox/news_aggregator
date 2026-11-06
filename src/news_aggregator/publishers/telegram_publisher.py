"""Издатель, публикующий уникальные сообщения в целевой Telegram-канал.

Единственный модуль, где происходит реальная отправка сообщений в
Telegram. Не публикует ничего сам по себе, кроме того, что явно передал
пайплайн уже после фильтрации и дедупликации — и никогда не удаляет
и не редактирует чужие сообщения (запрещено требованиями проекта).
"""

from __future__ import annotations

import logging
from typing import Any

from telethon import TelegramClient, utils
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    ChatForbiddenError,
    ChatRestrictedError,
    ChatWriteForbiddenError,
    FloodWaitError,
    ServerError,
    TimedOutError,
    UnauthorizedError,
    UserBannedInChannelError,
)

from news_aggregator.core.errors import (
    PartialPublishError,
    PublishUnavailableError,
    RateLimitedError,
)
from news_aggregator.core.interfaces import IPublisher
from news_aggregator.core.models import ProcessedMessage
from news_aggregator.sources.telegram_client import ensure_connected

logger = logging.getLogger(__name__)

# Ошибки, не зависящие от содержимого поста: опубликовать сейчас нельзя ничего.
# Список явный, а не ForbiddenError целиком: туда же входит, например,
# ChatSendMediaForbiddenError, из-за которой пост с фото блокировал бы источник.
# OSError покрывает ConnectionError и TimeoutError.
_UNAVAILABLE_ERRORS = (
    OSError,
    ServerError,
    TimedOutError,
    UnauthorizedError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    ChatForbiddenError,
    ChatRestrictedError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
)

# Лимиты Telegram. 1024 — подпись к медиа без Telegram Premium; считаем в единицах
# UTF-16, как и смещения entities: это не меньше, чем длина в символах.
_MAX_MESSAGE_LENGTH = 4096
_MAX_CAPTION_LENGTH = 1024


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _split_long_text(
    text: str, entities: list[Any], footer: str
) -> list[tuple[str, list[Any] | None]]:
    """Режет текст на части по лимиту сообщения, сохраняя форматирование.

    Подпись добавляется к последней части и резервируется заранее, поэтому она
    не отрывается от текста отдельным сообщением.
    """
    limit = _MAX_MESSAGE_LENGTH - _utf16_length(footer)
    parts = [
        (part, part_entities)
        for part, part_entities in utils.split_text(text, entities, limit=limit)
        if part.strip()
    ]
    last_text, last_entities = parts[-1]
    parts[-1] = (last_text + footer, last_entities)
    return parts


class TelegramPublisher(IPublisher):
    """Отправляет текст сообщения в целевой канал/чат через Telethon."""

    def __init__(self, client: TelegramClient, target_channel: str) -> None:
        if not target_channel:
            raise ValueError("target_channel не задан. Укажите TELEGRAM_TARGET_CHANNEL в .env")
        self._client = client
        self._target_channel = target_channel

    async def publish(self, message: ProcessedMessage) -> None:
        raw = message.raw
        entities = raw.formatting_entities

        # НЕ .strip() при наличии entities: offset/length в них считаются в
        # UTF-16 code units от начала исходного текста как есть — обрезка
        # краёв сдвинула бы их и сломала гиперссылки/форматирование.
        text = raw.text if entities else raw.text.strip()

        footer = f"\n\nИсточник: {raw.source_display_name}" if raw.source_display_name else ""

        # Добавленный футер идёт строго ПОСЛЕ исходного текста, поэтому
        # смещения entities (которые всегда внутри исходного текста)
        # остаются верными и после этого.
        full_text = text + footer if text.strip() else footer.strip()
        formatting_entities = list(entities) if entities else None
        media = raw.media

        limit = _MAX_MESSAGE_LENGTH if media is None else _MAX_CAPTION_LENGTH
        fits = _utf16_length(full_text) <= limit
        delivered = False

        try:
            await ensure_connected(self._client)
            try:
                if media is not None:
                    # send_file принимает media-объект напрямую (см. документацию
                    # Telethon: "A handle to an existing file... you can use its
                    # message.media as a file here") — Telegram копирует вложение
                    # на своей стороне, без скачивания и повторной загрузки файла
                    # агрегатором. Текст, который не влезает в подпись, идёт
                    # следом обычными сообщениями: их лимит вчетверо больше.
                    await self._client.send_file(
                        self._target_channel,
                        file=media.native_ref,
                        caption=full_text if fits else "",
                        parse_mode=None,
                        formatting_entities=formatting_entities if fits else None,
                    )
                    delivered = True

                if media is None or not fits:
                    parts: list[tuple[str, list[Any] | None]] = [(full_text, formatting_entities)]
                    if _utf16_length(full_text) > _MAX_MESSAGE_LENGTH:
                        parts = _split_long_text(text, list(entities), footer)
                    for part_text, part_entities in parts:
                        await self._client.send_message(
                            self._target_channel,
                            part_text,
                            # Превью — только у первого сообщения поста.
                            link_preview=bool(message.links) and not delivered,
                            parse_mode=None,
                            formatting_entities=part_entities or None,
                        )
                        delivered = True
            except Exception as exc:
                if delivered:
                    raise PartialPublishError(
                        f"{raw.source_id}/{raw.external_id} опубликован не полностью: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                raise
        except FloodWaitError as exc:
            # Длиннее flood_sleep_threshold Telethon (60 с) не пережидает сам.
            # Пайплайн поставит паузу и повторит это сообщение после неё.
            raise RateLimitedError(
                exc.seconds, f"FloodWait {exc.seconds} с при публикации в {self._target_channel}"
            ) from exc
        except _UNAVAILABLE_ERRORS as exc:
            raise PublishUnavailableError(
                f"{self._target_channel} недоступен: {type(exc).__name__}: {exc}"
            ) from exc

        logger.info(
            "Опубликовано в Telegram: %s/%s -> %s (медиа: %s, entities: %d)",
            raw.source_id,
            raw.external_id,
            self._target_channel,
            media.kind if media is not None else "нет",
            len(entities),
        )
