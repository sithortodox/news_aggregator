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
        text = message.raw.text.strip()
        media = message.raw.media

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
            )
        else:
            await self._client.send_message(
                self._target_channel,
                text,
                link_preview=bool(message.links),
            )

        logger.info(
            "Опубликовано в Telegram: %s/%s -> %s (медиа: %s)",
            message.raw.source_id,
            message.raw.external_id,
            self._target_channel,
            media.kind if media is not None else "нет",
        )
