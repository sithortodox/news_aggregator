from datetime import UTC, datetime

from news_aggregator.core.interfaces import IEmbeddingIndex
from news_aggregator.core.models import EmbeddingRecord


class InMemoryEmbeddingIndex(IEmbeddingIndex):
    def __init__(self) -> None:
        self.items: list[tuple[EmbeddingRecord, str, datetime]] = []

    async def find_recent(self, since: datetime) -> list[EmbeddingRecord]:
        return [record for record, _, created_at in self.items if created_at >= since]

    async def save(self, record: EmbeddingRecord, external_id: str) -> None:
        self.items.append((record, external_id, datetime.now(UTC)))

    async def purge_older_than(self, cutoff: datetime) -> int:
        before = len(self.items)
        self.items = [item for item in self.items if item[2] >= cutoff]
        return before - len(self.items)
