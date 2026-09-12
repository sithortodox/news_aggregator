"""Дедупликатор, определяющий дубли по совпадению ссылок в тексте.

Полезен, когда разные каналы по-разному переформулируют новость, но
ссылаются на один и тот же первоисточник.
"""

from __future__ import annotations

from news_aggregator.core.interfaces import IDeduplicator, IStorage
from news_aggregator.core.models import DeduplicationResult, ProcessedMessage


class LinkDeduplicator(IDeduplicator):
    """Считает сообщение дублем, если хотя бы одна из его ссылок уже
    встречалась ранее в других сообщениях."""

    def __init__(self, storage: IStorage) -> None:
        self._storage = storage

    @property
    def name(self) -> str:
        return "link_deduplicator"

    async def check(self, message: ProcessedMessage) -> DeduplicationResult:
        for link in message.links:
            if await self._storage.has_link(link):
                return DeduplicationResult(
                    is_duplicate=True,
                    reason=f"ссылка уже встречалась ранее: {link}",
                    matched_link=link,
                )
        return DeduplicationResult(is_duplicate=False, reason="ссылки новые или отсутствуют")

    async def remember(self, message: ProcessedMessage) -> None:
        for link in message.links:
            await self._storage.save_link(
                link,
                source_id=message.raw.source_id,
                external_id=message.raw.external_id,
            )
