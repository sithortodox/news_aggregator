"""Дедупликатор, определяющий дубли по совпадению ссылок в тексте.

Полезен, когда разные каналы по-разному переформулируют новость, но
ссылаются на один и тот же первоисточник.

Ссылки сравниваются в канонической форме (см. core/links.py): `utm_*`-метки,
`www`, http/https, слеш на конце и `?single` у постов Telegram не делают одну и
ту же статью «разными» ссылками. Ссылки НЕ на конкретный материал (главная
страница сайта, профиль канала `t.me/channel`) вообще не участвуют: такая
подпись есть почти в каждом посте канала, и иначе все посты после первого
считались бы дублями.

Гиперссылки на словах («Подробнее») проверяются тоже, но мягче: совпадение
считается дублем, только если ссылка пришла из ДРУГОГО источника. Под такими
словами часто прячется подпись канала («Мы в VK» -> vk.com/channel): у неё есть
путь, канонизация её не отсеет, и без этого правила все посты канала после
первого стали бы дублями. Первоисточник, на который сослались два разных канала,
при этом находится.
"""

from __future__ import annotations

from news_aggregator.core.interfaces import IDeduplicator, IStorage
from news_aggregator.core.links import canonical_content_links, entity_urls
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

        visible = {canonical for _, canonical in canonical_content_links(message.links)}
        for raw, canonical in canonical_content_links(entity_urls(message)):
            if canonical in visible:
                continue
            seen_in = await self._storage.get_link_source(canonical)
            if seen_in is not None and seen_in != message.raw.source_id:
                return DeduplicationResult(
                    is_duplicate=True,
                    reason=f"гиперссылка уже встречалась в источнике {seen_in}: {raw}",
                    matched_link=raw,
                )
        return DeduplicationResult(is_duplicate=False, reason="ссылки новые или отсутствуют")

    async def remember(self, message: ProcessedMessage) -> None:
        links = (*message.links, *entity_urls(message))
        for _, canonical in canonical_content_links(links):
            await self._storage.save_link(
                canonical,
                source_id=message.raw.source_id,
                external_id=message.raw.external_id,
            )
