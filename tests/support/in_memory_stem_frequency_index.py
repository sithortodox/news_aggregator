from collections import Counter

from news_aggregator.core.interfaces import IStemFrequencyIndex


class InMemoryStemFrequencyIndex(IStemFrequencyIndex):
    def __init__(self, total: int = 0, counts: dict[str, int] | None = None) -> None:
        self.total = total
        self.counts: Counter[str] = Counter(counts or {})

    async def load(self) -> tuple[int, dict[str, int]]:
        return self.total, dict(self.counts)

    async def add(self, stems: frozenset[str]) -> None:
        self.total += 1
        self.counts.update(stems)
