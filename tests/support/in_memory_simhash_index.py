"""In-memory двойник ISimhashIndex для юнит-тестов дедупликатора.

Не часть production-кода: используется только в tests/.
"""

from __future__ import annotations

from datetime import datetime


class InMemorySimhashIndex:
    def __init__(self) -> None:
        self._entries: list[tuple[int, str, str, datetime]] = []

    async def find_recent(self, since: datetime) -> list[int]:
        return [simhash for simhash, _, _, created_at in self._entries if created_at >= since]

    async def save(self, simhash: int, source_id: str, external_id: str) -> None:
        # В реальной SQLite-реализации метка времени берётся из datetime.now(UTC)
        # в момент вставки; здесь для управляемости тестов её тоже фиксируем
        # на момент вызова, но тесты, которым нужен контроль над "давностью"
        # записи, используют save_at.
        from datetime import UTC

        self._entries.append((simhash, source_id, external_id, datetime.now(UTC)))

    async def save_at(
        self, simhash: int, source_id: str, external_id: str, created_at: datetime
    ) -> None:
        """Тестовый хелпер: сохранить запись с явно заданной меткой времени
        (например, "12 часов назад"), чтобы проверить работу окна сравнения."""
        self._entries.append((simhash, source_id, external_id, created_at))

    async def purge_older_than(self, cutoff: datetime) -> int:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e[3] >= cutoff]
        return before - len(self._entries)

    async def close(self) -> None:
        pass
