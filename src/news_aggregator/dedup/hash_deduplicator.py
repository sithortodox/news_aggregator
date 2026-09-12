"""Дедупликатор, определяющий дубли по sha256-хэшу нормализованного текста."""

from __future__ import annotations

from news_aggregator.core.interfaces import IDeduplicator, IStorage
from news_aggregator.core.models import DeduplicationResult, ProcessedMessage


class TextHashDeduplicator(IDeduplicator):
    """Считает сообщение дублем, если его text_hash уже встречался ранее.

    Работает через IStorage и ничего не знает о том, что состояние на самом
    деле хранится в SQLite.
    """

    def __init__(self, storage: IStorage) -> None:
        self._storage = storage

    @property
    def name(self) -> str:
        return "text_hash_deduplicator"

    async def check(self, message: ProcessedMessage) -> DeduplicationResult:
        if await self._storage.has_hash(message.text_hash):
            return DeduplicationResult(
                is_duplicate=True,
                reason=f"текст уже встречался ранее (hash={message.text_hash[:12]}...)",
                matched_hash=message.text_hash,
            )
        return DeduplicationResult(is_duplicate=False, reason="текст новый")

    async def remember(self, message: ProcessedMessage) -> None:
        await self._storage.save_hash(
            message.text_hash,
            source_id=message.raw.source_id,
            external_id=message.raw.external_id,
        )
