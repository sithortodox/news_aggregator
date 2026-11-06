"""Источник сообщений из реального Telegram через Telethon.

Единственный модуль (вместе с telegram_client.py), где происходит прямое
общение с Telegram API. Не содержит бизнес-логики фильтрации/дедупликации —
только отдаёт сырые сообщения через интерфейс ISourceReader, как и
FakeSourceReader.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from datetime import UTC, datetime
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from news_aggregator.core.errors import RateLimitedError
from news_aggregator.core.interfaces import ISourceReader
from news_aggregator.core.models import MediaAttachment, RawMessage, Source, SourceKind
from news_aggregator.sources.telegram_client import ensure_connected

logger = logging.getLogger(__name__)

_TELEGRAM_KINDS = (SourceKind.TELEGRAM_CHANNEL, SourceKind.TELEGRAM_GROUP)
_ALBUM_SETTLE_SECONDS = 10.0


def _attachment(tg_message: Any) -> MediaAttachment | None:
    """Вложение сообщения или None, если публиковать нечего.

    Стикеры и сервисные сообщения не контент: `.photo` у Telethon отдаёт и новую
    аватарку канала из сервисного сообщения о её смене.
    """
    if getattr(tg_message, "action", None) is not None:
        return None
    if getattr(tg_message, "sticker", None) is not None:
        return None

    # ВАЖНО: используем именно resolved-объекты .photo/.document, а
    # не tg_message.media напрямую как native_ref. Telethon эти
    # property'и резолвит и внутрь превью ссылки (web_preview),
    # если у него есть встроенная картинка/видео — то есть .photo
    # может быть не None, даже когда tg_message.media остаётся
    # MessageMediaWebPage. send_file(..., file=MessageMediaWebPage)
    # падает с TypeError, а send_file(..., file=<Photo>/<Document>)
    # работает и для превью, и для настоящих вложений.
    for kind in ("photo", "document"):
        native_ref = getattr(tg_message, kind, None)
        if native_ref is not None:
            fingerprint = f"{kind}:{getattr(native_ref, 'id', '')}"
            return MediaAttachment(kind=kind, native_ref=native_ref, fingerprint=fingerprint)
    return None


def _merge_attachments(attachments: list[MediaAttachment]) -> MediaAttachment | None:
    """Одно вложение как есть; несколько — альбом: send_file принимает список."""
    if len(attachments) <= 1:
        return attachments[0] if attachments else None
    return MediaAttachment(
        kind="album",
        native_ref=[attachment.native_ref for attachment in attachments],
        fingerprint=",".join(attachment.fingerprint for attachment in attachments),
    )


def _to_raw_message(
    tg_messages: list[Any], source: Source, fetched_at: datetime
) -> RawMessage | None:
    """Собирает пост из сообщения или альбома; None — публиковать нечего.

    external_id — последнего сообщения: курсор должен встать за весь альбом.
    Текст альбома лежит в одном из сообщений, не обязательно в первом.
    """
    media = _merge_attachments([a for a in map(_attachment, tg_messages) if a is not None])
    captioned = next(
        (m for m in tg_messages if (getattr(m, "message", None) or "").strip()),
        None,
    )
    if captioned is None and media is None:
        # Сервисное сообщение, стикер, опрос и т.п.
        logger.debug(
            "Пропущено сообщение без содержимого %s в источнике '%s'",
            tg_messages[-1].id,
            source.id,
        )
        return None

    return RawMessage(
        source_id=source.id,
        external_id=str(tg_messages[-1].id),
        text=captioned.message if captioned is not None else "",
        posted_at=tg_messages[0].date or fetched_at,
        fetched_at=fetched_at,
        media=media,
        formatting_entities=tuple(getattr(captioned, "entities", None) or ()),
        source_display_name=source.display_name,
    )


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
        """Отдаёт новые сообщения; FloodWait Telegram переводит в RateLimitedError.

        Telethon сам молча пережидает FloodWait до flood_sleep_threshold (по
        умолчанию 60 с). Сюда доходят только более длинные — их пайплайн
        обрабатывает паузой, а не повторами (повторы только удлиняют FloodWait).
        """
        try:
            async with aclosing(self._read(source, since_external_id)) as messages:
                async for message in messages:
                    yield message
        except FloodWaitError as exc:
            raise RateLimitedError(
                exc.seconds, f"FloodWait {exc.seconds} с при чтении '{source.id}'"
            ) from exc

    async def _read(
        self, source: Source, since_external_id: str | None
    ) -> AsyncGenerator[RawMessage, None]:
        fetched_at = datetime.now(tz=UTC)

        await ensure_connected(self._client)

        try:
            # Не get_entity: со строкой он при каждом вызове отправляет
            # ResolveUsernameRequest, а этот запрос Telegram лимитирует жёстко.
            # get_input_entity берёт канал из кэша сессии, в сеть идёт один раз.
            entity = await self._client.get_input_entity(source.identifier)
        except FloodWaitError:
            raise  # не «не удалось получить сущность»: обработает read_new_messages
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

        # Сообщения одного альбома идут подряд с общим grouped_id: копим их и
        # отдаём одним постом, когда встретится что-то другое.
        album: list[Any] = []
        async for tg_message in self._client.iter_messages(entity, min_id=min_id, reverse=True):
            grouped_id = getattr(tg_message, "grouped_id", None)
            if album and grouped_id != album[0].grouped_id:
                raw = _to_raw_message(album, source, fetched_at)
                album = []
                if raw is not None:
                    yield raw

            if grouped_id is not None:
                album.append(tg_message)
                continue

            raw = _to_raw_message([tg_message], source, fetched_at)
            if raw is not None:
                yield raw

        # Части альбома приходят не одновременно. Свежий альбом в самом конце
        # канала мог дойти не весь: курсор останется перед ним, следующий
        # прогон прочитает его целиком.
        if album and (fetched_at - album[-1].date).total_seconds() >= _ALBUM_SETTLE_SECONDS:
            raw = _to_raw_message(album, source, fetched_at)
            if raw is not None:
                yield raw
