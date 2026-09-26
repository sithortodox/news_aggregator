"""Издатель, публикующий уникальные сообщения в целевой Telegram-канал.

Единственный модуль, где происходит реальная отправка сообщений в
Telegram. Не публикует ничего сам по себе, кроме того, что явно передал
пайплайн уже после фильтрации и дедупликации — и никогда не удаляет
и не редактирует чужие сообщения (запрещено требованиями проекта).
"""

from __future__ import annotations

import logging

from telethon import TelegramClient

from news_aggregator.core.interfaces import IPublisher
from news_aggregator.core.models import ProcessedMessage

logger = logging.getLogger(__name__)


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

        if raw.source_display_name:
            text = f"{text}\n\nИсточник: {raw.source_display_name}"

        # Добавленный футер идёт строго ПОСЛЕ исходного текста, поэтому
        # смещения entities (которые всегда внутри исходного текста)
        # остаются верными и после этого.
        formatting_entities = list(entities) if entities else None
        media = raw.media

        if media is not None:
            # send_file принимает media-объект напрямую (см. документацию
            # Telethon: "A handle to an existing file... you can use its
            # message.media as a file here") — Telegram копирует вложение
            # на своей стороне, без скачивания и повторной загрузки файла
            # агрегатором.
            await self._client.send_file(
                self._target_channel,
                file=media.native_ref,
                caption=text,
                parse_mode=None,
                formatting_entities=formatting_entities,
            )
        else:
            await self._client.send_message(
                self._target_channel,
                text,
                link_preview=bool(message.links),
                parse_mode=None,
                formatting_entities=formatting_entities,
            )

        logger.info(
            "Опубликовано в Telegram: %s/%s -> %s (медиа: %s, entities: %d)",
            raw.source_id,
            raw.external_id,
            self._target_channel,
            media.kind if media is not None else "нет",
            len(entities),
        )
