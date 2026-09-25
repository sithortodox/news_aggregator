"""Источник сообщений из реального Telegram через Telethon.

Единственный модуль (вместе с telegram_client.py), где происходит прямое
общение с Telegram API. Не содержит бизнес-логики фильтрации/дедупликации —
только отдаёт сырые сообщения через интерфейс ISourceReader, как и
FakeSourceReader.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from telethon import TelegramClient

from news_aggregator.core.interfaces import ISourceReader
from news_aggregator.core.models import MediaAttachment, RawMessage, Source, SourceKind

logger = logging.getLogger(__name__)

_TELEGRAM_KINDS = (SourceKind.TELEGRAM_CHANNEL, SourceKind.TELEGRAM_GROUP)


class TelegramSourceReader(ISourceReader):
    """Читает новые сообщения Telegram-канала/группы начиная с курсора.

    Использует Telethon's iter_messages(min_id=..., reverse=True), чтобы
    получать сообщения строго после last_external_id и в хронологическом
    порядке (от старых к новым) — это важно для корректного продвижения
    курсора при частичных сбоях.
    """

    def __init__(self, client: TelegramClient) -> None:
        self._client = client

    def supports(self, source: Source) -> bool:
        return source.kind in _TELEGRAM_KINDS

    async def read_new_messages(
        self, source: Source, since_external_id: str | None
    ) -> AsyncIterator[RawMessage]:
        fetched_at = datetime.now(tz=UTC)

        try:
            entity = await self._client.get_entity(source.identifier)
        except Exception:
            logger.exception(
                "Не удалось получить сущность Telegram для источника '%s' (%s)",
                source.id,
                source.identifier,
            )
            raise

        if since_external_id is not None:
            min_id = int(since_external_id)
        else:
            # Источник читается впервые (только что добавлен, сохранённого
            # курсора ещё нет) — не вычитываем всю историю канала: для
            # старого высокоактивного канала это сотни/тысячи сообщений и
            # практически гарантированный FloodWaitError при попытке
            # опубликовать их все разом. Вместо этого фиксируем текущее
            # последнее сообщение как стартовую точку — дальше видны
            # только действительно новые посты.
            latest = await self._client.get_messages(entity, limit=1)
            min_id = latest[0].id - 1 if latest else 0

        async for tg_message in self._client.iter_messages(entity, min_id=min_id, reverse=True):
            text = getattr(tg_message, "message", None) or ""
            if not text.strip():
                # Пропускаем сообщения без текста (только фото/стикер/сервисные).
                logger.debug(
                    "Пропущено нетекстовое сообщение %s в источнике '%s'",
                    tg_message.id,
                    source.id,
                )
                continue

            posted_at = tg_message.date or fetched_at
            media = None
            # Используем property-обёртки Telethon (.photo/.document), а не
            # сырое tg_message.media напрямую: .media может быть непустым и
            # для превью ссылки (MessageMediaWebPage), опроса, геолокации и
            # т.п. — это не файл, send_file с таким объектом падает с
            # TypeError. .photo/.document возвращают None для всего, что не
            # является настоящим загружаемым вложением.
            if getattr(tg_message, "photo", None) is not None:
                media = MediaAttachment(kind="photo", native_ref=tg_message.media)
            elif getattr(tg_message, "document", None) is not None:
                media = MediaAttachment(kind="document", native_ref=tg_message.media)

            yield RawMessage(
                source_id=source.id,
                external_id=str(tg_message.id),
                text=text,
                posted_at=posted_at,
                fetched_at=fetched_at,
                media=media,
            )
