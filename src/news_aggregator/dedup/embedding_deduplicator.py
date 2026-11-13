"""Дедупликатор по смыслу: пересказ одного события разными словами.

Ловит то, что лексические слои не могут: каналы пишут об одном событии каждый
со своими деталями. Например, «сильнейший с 1967 года ливень» у naebnet (циклон
«Роми», 54% нормы) и у trendrunet (300 Черкизовских прудов, начало в 16:00):
общих слов семь из пятидесяти, Жаккар 0.14.

Метод: текст без подписи канала переводится в смысловой вектор
(ITextEmbedder) и сравнивается по косинусу с векторами постов за
lookback_hours. Дубль, если косинус не меньше min_similarity и выполнены обе
защиты:

* **Другой источник.** У постов одного канала похожий стиль и рубрики
  («Полезное на kod.ru» за разные недели), а пересказывать себя каналы не
  склонны.
* **Есть общие слова** (min_shared_stems основ). Подборки и дайджесты разных
  каналов модель считает похожими по жанру, но общих слов у них почти нет.

С dry_run (по умолчанию включён) слой ничего не отбрасывает, а пишет в лог
«пробно», с каким каналом и косинусом склеил бы.

Проверка на 1100 постах ленты за 10 дней: при min_similarity 0.80 с защитами —
18 склеек, из них около 15 настоящих пересказов (первый снег в Москве, ливень,
GTA 6 в 30 FPS, Beget, «Наруто», кошки и мяуканье, отставка Хуанга).
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

from news_aggregator.core.interfaces import IDeduplicator, IEmbeddingIndex, ITextEmbedder
from news_aggregator.core.lemmatize import extract_lemma_set, stem_set
from news_aggregator.core.models import DeduplicationResult, EmbeddingRecord, ProcessedMessage
from news_aggregator.core.text_utils import normalize_text, strip_signature

logger = logging.getLogger(__name__)

# Вектор, посчитанный в check(), нужен ещё в remember() того же сообщения.
_MAX_CACHED_VECTORS = 256


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


class EmbeddingDeduplicator(IDeduplicator):
    """Считает сообщение дублем, если близкий по смыслу пост недавно вышел из
    другого источника (см. описание модуля)."""

    def __init__(
        self,
        index: IEmbeddingIndex,
        embedder: ITextEmbedder | None,
        min_similarity: float = 0.80,
        lookback_hours: float = 24.0,
        min_text_stems: int = 4,
        min_shared_stems: int = 3,
        stem_length: int | None = 5,
        dry_run: bool = True,
    ) -> None:
        if not (0.0 < min_similarity <= 1.0):
            raise ValueError("min_similarity должен быть в диапазоне (0.0, 1.0]")
        if lookback_hours <= 0:
            raise ValueError("lookback_hours должен быть положительным")
        if min_text_stems < 1:
            raise ValueError("min_text_stems должен быть положительным")
        if min_shared_stems < 0:
            raise ValueError("min_shared_stems не может быть отрицательным")
        if stem_length is not None and stem_length < 2:
            raise ValueError("stem_length должен быть >= 2 или null (без усечения)")
        self._index = index
        self._embedder = embedder
        self._min_similarity = min_similarity
        self._lookback_hours = lookback_hours
        self._min_text_stems = min_text_stems
        self._min_shared = min_shared_stems
        self._stem_length = stem_length
        self._dry_run = dry_run
        self._vectors: OrderedDict[tuple[str, str], tuple[float, ...]] = OrderedDict()

    @property
    def name(self) -> str:
        return "embedding_deduplicator"

    def _since(self) -> datetime:
        return datetime.now(UTC) - timedelta(hours=self._lookback_hours)

    @staticmethod
    def _text(message: ProcessedMessage) -> str:
        return strip_signature(message.raw.text)

    def _stems(self, message: ProcessedMessage) -> frozenset[str]:
        return stem_set(extract_lemma_set(normalize_text(self._text(message))), self._stem_length)

    async def _vector(self, message: ProcessedMessage) -> tuple[float, ...] | None:
        key = (message.raw.source_id, message.raw.external_id)
        if key in self._vectors:
            return self._vectors[key]
        if self._embedder is None:
            return None
        try:
            vector = await asyncio.to_thread(self._embedder.embed, self._text(message))
        except Exception:
            # Без вектора сообщение проверяют остальные слои; терять его нельзя.
            logger.warning("Не удалось посчитать смысловой вектор", exc_info=True)
            return None
        self._vectors[key] = vector
        if len(self._vectors) > _MAX_CACHED_VECTORS:
            self._vectors.popitem(last=False)
        return vector

    async def check(self, message: ProcessedMessage) -> DeduplicationResult:
        if self._embedder is None:
            return DeduplicationResult(
                is_duplicate=False, reason="модель для сравнения не загружена"
            )
        stems = self._stems(message)
        if len(stems) < self._min_text_stems:
            return DeduplicationResult(
                is_duplicate=False,
                reason=f"текст слишком короткий ({len(stems)} < {self._min_text_stems} основ)",
            )
        vector = await self._vector(message)
        if vector is None:
            return DeduplicationResult(is_duplicate=False, reason="вектор не посчитан")

        best: float | None = None
        match: tuple[float, EmbeddingRecord, int] | None = None
        for candidate in await self._index.find_recent(self._since()):
            if candidate.source_id == message.raw.source_id:
                continue
            similarity = _cosine(vector, candidate.vector)
            if best is None or similarity > best:
                best = similarity
            if similarity < self._min_similarity:
                continue
            shared = len(stems & candidate.stems)
            if shared < self._min_shared:
                continue
            if match is None or similarity > match[0]:
                match = (similarity, candidate, shared)

        if match is not None:
            similarity, candidate, shared = match
            explanation = (
                f"по смыслу то же, что пост из '{candidate.source_id}' "
                f"(косинус {similarity:.2f} >= {self._min_similarity}, общих основ {shared}, "
                f"окно={self._lookback_hours}ч)"
            )
            if not self._dry_run:
                return DeduplicationResult(is_duplicate=True, reason=explanation)
            logger.info(
                "По смыслу (пробно, не отброшено): %s/%s — %s",
                message.raw.source_id,
                message.raw.external_id,
                explanation,
            )
            return DeduplicationResult(is_duplicate=False, reason=f"пробно: {explanation}")

        detail = f", ближайший косинус {best:.2f}" if best is not None else ""
        return DeduplicationResult(
            is_duplicate=False, reason=f"близких по смыслу постов из других каналов нет{detail}"
        )

    async def remember(self, message: ProcessedMessage) -> None:
        stems = self._stems(message)
        if len(stems) < self._min_text_stems:
            self._vectors.pop((message.raw.source_id, message.raw.external_id), None)
            return
        vector = await self._vector(message)
        self._vectors.pop((message.raw.source_id, message.raw.external_id), None)
        if vector is None:
            return
        await self._index.save(
            EmbeddingRecord(vector=vector, source_id=message.raw.source_id, stems=stems),
            external_id=message.raw.external_id,
        )
        deleted = await self._index.purge_older_than(self._since())
        if deleted:
            logger.debug(
                "Удалено %d устаревших векторов (старше %sч)", deleted, self._lookback_hours
            )
