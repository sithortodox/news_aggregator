"""In-memory двойник IFileHashIndex для юнит-тестов FileHashDeduplicator.

Не часть production-кода: используется только в tests/.
"""

from __future__ import annotations

from datetime import UTC, datetime

from news_aggregator.core.interfaces import IFileHashIndex
from news_aggregator.core.models import FileHashRecord


class InMemoryFileHashIndex(IFileHashIndex):
    def __init__(self) -> None:
        self.entries: list[tuple[FileHashRecord, str, datetime]] = []

    async def find_recent(self, since: datetime) -> list[FileHashRecord]:
        return [record for record, _, created_at in self.entries if created_at >= since]

    async def save(self, record: FileHashRecord, external_id: str) -> None:
        self.entries.append((record, external_id, datetime.now(UTC)))

    async def purge_older_than(self, cutoff: datetime) -> int:
        before = len(self.entries)
        self.entries = [entry for entry in self.entries if entry[2] >= cutoff]
        return before - len(self.entries)
