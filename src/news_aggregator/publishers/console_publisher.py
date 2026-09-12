"""Издатель, публикующий сообщения в консоль (stdout через logging).

Полезен для MVP, отладки и режимов, где реальная публикация в Telegram
не нужна или ещё не настроена.
"""

from __future__ import annotations

import logging

from news_aggregator.core.interfaces import IPublisher
from news_aggregator.core.models import ProcessedMessage

logger = logging.getLogger("news_aggregator.publish")


class ConsolePublisher(IPublisher):
    """Печатает опубликованные сообщения в лог/консоль."""

    def __init__(self, prefix: str = "[NEWS]") -> None:
        self._prefix = prefix

    async def publish(self, message: ProcessedMessage) -> None:
        links_part = f" | ссылки: {', '.join(message.links)}" if message.links else ""
        logger.info(
            "%s [%s/%s] %s%s",
            self._prefix,
            message.raw.source_id,
            message.raw.external_id,
            message.raw.text.strip(),
            links_part,
        )
