"""In-memory двойник ISourceRepository для юнит-тестов пайплайна и бота.

Не часть production-кода: используется только в tests/.
"""

from __future__ import annotations

from news_aggregator.core.interfaces import ISourceRepository
from news_aggregator.core.models import Source


class InMemorySourceRepository(ISourceRepository):
    def __init__(self, initial: list[Source] | None = None) -> None:
        self._sources: dict[str, Source] = {s.id: s for s in (initial or [])}

    async def list_sources(self) -> list[Source]:
        return list(self._sources.values())

    async def get_source(self, source_id: str) -> Source | None:
        return self._sources.get(source_id)

    async def add_source(self, source: Source) -> bool:
        if source.id in self._sources:
            return False
        if any(s.identifier == source.identifier for s in self._sources.values()):
            return False
        self._sources[source.id] = source
        return True

    async def remove_source(self, source_id: str) -> bool:
        return self._sources.pop(source_id, None) is not None

    async def set_enabled(self, source_id: str, enabled: bool) -> bool:
        source = self._sources.get(source_id)
        if source is None:
            return False
        from dataclasses import replace

        self._sources[source_id] = replace(source, enabled=enabled)
        return True

    async def close(self) -> None:
        pass
