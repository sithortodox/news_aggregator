"""Фейковый источник сообщений.

Используется в MVP и в интеграционных тестах, когда реальный Telegram
недоступен или нежелателен (например, в CI). Генерирует небольшой набор
детерминированных сообщений, включая намеренные дубли — удобно для
проверки работы дедупликации "из коробки".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta

from news_aggregator.core.interfaces import ISourceReader
from news_aggregator.core.models import RawMessage, Source, SourceKind

_DEMO_TEXTS: tuple[str, ...] = (
    "Центробанк повысил ключевую ставку до 18%. Подробнее: https://example.com/news/1",
    "ЦБ поднял ключевую ставку до 18%! Подробности тут: https://example.com/news/1",
    "Открылась новая линия метро в центре города, движение поездов началось сегодня утром",
    "Скидка 50% на все товары только сегодня, промокод внутри!",
    "кор",
    "Учёные обнаружили новый вид бабочек в тропических лесах Амазонии",
)


class FakeSourceReader(ISourceReader):
    """Возвращает заранее заданный набор демонстрационных сообщений.

    Поддерживает инкрементальное чтение по external_id, как и реальный
    Telegram-адаптер, чтобы пайплайн не отличал фейковый источник от
    настоящего.
    """

    def supports(self, source: Source) -> bool:
        return source.kind == SourceKind.FAKE

    async def read_new_messages(
        self, source: Source, since_external_id: str | None
    ) -> AsyncIterator[RawMessage]:
        base_time = datetime.now()
        last_seen = int(since_external_id) if since_external_id else 0

        for index, text in enumerate(_DEMO_TEXTS, start=1):
            if index <= last_seen:
                continue
            yield RawMessage(
                source_id=source.id,
                external_id=str(index),
                text=text,
                posted_at=base_time - timedelta(minutes=len(_DEMO_TEXTS) - index),
                fetched_at=base_time,
                source_display_name=source.display_name,
            )
