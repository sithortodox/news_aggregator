"""In-memory двойник IStorage для юнит-тестов дедупликаторов и пайплайна.

Не часть production-кода: используется только в tests/, чтобы проверять
логику дедупликации и пайплайна без реальной SQLite-базы.
"""

from __future__ import annotations

from news_aggregator.core.interfaces import IStorage


class InMemoryStorage(IStorage):
    def __init__(self) -> None:
        self._cursors: dict[str, str] = {}
        self._hashes: set[str] = set()
        self._links: set[str] = set()

    async def get_last_external_id(self, source_id: str) -> str | None:
        return self._cursors.get(source_id)

    async def set_last_external_id(self, source_id: str, external_id: str) -> None:
        self._cursors[source_id] = external_id

    async def has_hash(self, text_hash: str) -> bool:
        return text_hash in self._hashes

    async def save_hash(self, text_hash: str, source_id: str, external_id: str) -> None:
        self._hashes.add(text_hash)

    async def has_link(self, link: str) -> bool:
        return link in self._links

    async def save_link(self, link: str, source_id: str, external_id: str) -> None:
        self._links.add(link)

    async def close(self) -> None:
        pass
