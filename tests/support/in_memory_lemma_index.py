"""In-memory двойник ILemmaSetIndex для юнит-тестов ParaphraseDeduplicator.

Не часть production-кода: используется только в tests/.
"""

from __future__ import annotations

from datetime import datetime


class InMemoryLemmaIndex:
    def __init__(self) -> None:
        self._entries: list[tuple[frozenset[str], str, str, datetime]] = []

    async def find_recent(self, since: datetime) -> list[frozenset[str]]:
        return [lemmas for lemmas, _, _, created_at in self._entries if created_at >= since]

    async def save(self, lemmas: frozenset[str], source_id: str, external_id: str) -> None:
        from datetime import UTC

        self._entries.append((lemmas, source_id, external_id, datetime.now(UTC)))

    async def save_at(
        self, lemmas: frozenset[str], source_id: str, external_id: str, created_at: datetime
    ) -> None:
        """Тестовый хелпер: сохранить запись с явно заданной меткой времени."""
        self._entries.append((lemmas, source_id, external_id, created_at))

    async def purge_older_than(self, cutoff: datetime) -> int:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e[3] >= cutoff]
        return before - len(self._entries)

    async def close(self) -> None:
        pass
