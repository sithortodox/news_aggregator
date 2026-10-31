"""Дедупликатор, определяющий дубли по совпадению ссылок в тексте.

Полезен, когда разные каналы по-разному переформулируют новость, но
ссылаются на один и тот же первоисточник.

Ссылки сравниваются в канонической форме (см. core/links.py): `utm_*`-метки,
`www`, http/https, слеш на конце и `?single` у постов Telegram не делают одну и
ту же статью «разными» ссылками. Ссылки НЕ на конкретный материал (главная
страница сайта, профиль канала `t.me/channel`) вообще не участвуют: такая
подпись есть почти в каждом посте канала, и иначе все посты после первого
считались бы дублями.
"""

from __future__ import annotations

from news_aggregator.core.interfaces import IDeduplicator, IStorage
from news_aggregator.core.links import canonical_content_links
from news_aggregator.core.models import DeduplicationResult, ProcessedMessage


class LinkDeduplicator(IDeduplicator):
    """Считает сообщение дублем, если хотя бы одна из его ссылок на конкретный
    материал уже встречалась ранее в других сообщениях."""

    def __init__(self, storage: IStorage) -> None:
        self._storage = storage

    @property
    def name(self) -> str:
        return "link_deduplicator"

    async def check(self, message: ProcessedMessage) -> DeduplicationResult:
        for raw, canonical in canonical_content_links(message.links):
            # Сырая форма проверяется тоже: записи, сохранённые до введения
            # канонизации, лежат в базе в исходном виде.
            if await self._storage.has_link(canonical) or (
                raw != canonical and await self._storage.has_link(raw)
            ):
                return DeduplicationResult(
                    is_duplicate=True,
                    reason=f"ссылка уже встречалась ранее: {raw}",
                    matched_link=raw,
                )
        return DeduplicationResult(is_duplicate=False, reason="ссылки новые или отсутствуют")

    async def remember(self, message: ProcessedMessage) -> None:
        for _, canonical in canonical_content_links(message.links):
            await self._storage.save_link(
                canonical,
                source_id=message.raw.source_id,
                external_id=message.raw.external_id,
            )
