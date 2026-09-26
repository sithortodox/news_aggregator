"""Дедупликатор, определяющий почти-дубли по SimHash в ограниченном окне.

В отличие от TextHashDeduplicator (точное совпадение нормализованного
текста) и LinkDeduplicator (точное совпадение ссылки), этот дедупликатор
ловит сообщения, которые почти дословно совпадают, но не идентичны
побитово: лишний знак препинания, приписка источника в конце, случайный
эмодзи, который не убрала нормализация, и т.п.

Это НЕ семантическая дедупликация — два разных пересказа одного события
разными словами SimHash, скорее всего, не поймает (для этого нужны
эмбеддинги, см. README/бэклог). Задача именно этого компонента — почти
дословные повторы.

Сравнение ведётся только с сообщениями за последние lookback_hours часов:
без этого индекс отпечатков рос бы бесконечно, а с ростом также росла бы
(линейно) стоимость каждой проверки, поскольку сравнение — это полный
перебор кандидатов за окно (для реалистичных объёмов персонального
агрегатора счёт идёт на сотни-тысячи сообщений за окно, что более чем
приемлемо; для существенно больших объёмов потребовалась бы LSH-индексация
вместо полного перебора).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from news_aggregator.core.interfaces import IDeduplicator, ISimhashIndex
from news_aggregator.core.models import DeduplicationResult, ProcessedMessage
from news_aggregator.core.simhash import SimhashUnit, compute_simhash, hamming_distance

logger = logging.getLogger(__name__)


class SimHashDeduplicator(IDeduplicator):
    """Считает сообщение дублем, если в окне lookback_hours нашёлся другой
    отпечаток на расстоянии Хэмминга <= max_hamming_distance."""

    def __init__(
        self,
        index: ISimhashIndex,
        max_hamming_distance: int = 4,
        lookback_hours: float = 12.0,
        shingle_size: int = 3,
        unit: SimhashUnit = "char",
    ) -> None:
        if max_hamming_distance < 0:
            raise ValueError("max_hamming_distance не может быть отрицательным")
        if lookback_hours <= 0:
            raise ValueError("lookback_hours должен быть положительным")
        if unit not in ("char", "word"):
            raise ValueError(f"unit должен быть 'char' или 'word', получено: {unit!r}")
        self._index = index
        self._max_hamming_distance = max_hamming_distance
        self._lookback_hours = lookback_hours
        self._shingle_size = shingle_size
        self._unit = unit

    @property
    def name(self) -> str:
        return "simhash_deduplicator"

    def _since(self) -> datetime:
        return datetime.now(UTC) - timedelta(hours=self._lookback_hours)

    async def check(self, message: ProcessedMessage) -> DeduplicationResult:
        if not message.normalized_text:
            return DeduplicationResult(is_duplicate=False, reason="пустой текст, нечего сравнивать")

        simhash = compute_simhash(
            message.normalized_text, shingle_size=self._shingle_size, unit=self._unit
        )
        candidates = await self._index.find_recent(self._since())

        best_distance: int | None = None
        for candidate in candidates:
            distance = hamming_distance(simhash, candidate)
            if best_distance is None or distance < best_distance:
                best_distance = distance
            if distance <= self._max_hamming_distance:
                return DeduplicationResult(
                    is_duplicate=True,
                    reason=(
                        f"почти-дубль по SimHash (расстояние Хэмминга={distance} "
                        f"<= {self._max_hamming_distance}, окно={self._lookback_hours}ч)"
                    ),
                )

        detail = ""
        if best_distance is not None:
            detail = f", ближайший кандидат на расстоянии {best_distance}"
        return DeduplicationResult(
            is_duplicate=False, reason=f"похожих сообщений не найдено{detail}"
        )

    async def remember(self, message: ProcessedMessage) -> None:
        if not message.normalized_text:
            return
        simhash = compute_simhash(
            message.normalized_text, shingle_size=self._shingle_size, unit=self._unit
        )
        await self._index.save(
            simhash, source_id=message.raw.source_id, external_id=message.raw.external_id
        )
        # Не даём индексу расти бесконечно: всё, что вышло за окно сравнения,
        # больше никогда не понадобится (см. docstring модуля).
        deleted = await self._index.purge_older_than(self._since())
        if deleted:
            logger.debug(
                "Удалено %d устаревших SimHash-отпечатков (старше %sч)",
                deleted,
                self._lookback_hours,
            )
