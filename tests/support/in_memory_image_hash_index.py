"""In-memory двойник IImageHashIndex для юнит-тестов ImageDeduplicator.

Не часть production-кода: используется только в tests/.
"""

from __future__ import annotations

from datetime import UTC, datetime

from news_aggregator.core.interfaces import IImageHashIndex
from news_aggregator.core.models import ImageHashRecord


class InMemoryImageHashIndex(IImageHashIndex):
    def __init__(self) -> None:
        self.entries: list[tuple[ImageHashRecord, str, datetime]] = []

    async def find_recent(self, since: datetime) -> list[ImageHashRecord]:
        return [record for record, _, created_at in self.entries if created_at >= since]

    async def save(self, record: ImageHashRecord, external_id: str) -> None:
        self.entries.append((record, external_id, datetime.now(UTC)))

    async def purge_older_than(self, cutoff: datetime) -> int:
        before = len(self.entries)
        self.entries = [entry for entry in self.entries if entry[2] >= cutoff]
        return before - len(self.entries)
