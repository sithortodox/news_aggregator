"""Дедупликатор по картинке: одна и та же картинка в постах разных каналов.

Ловит то, что текстовые слои не могут: два канала выложили один скриншот
с разными подписями («ЖИЗА: к Дурову не засылают шпионок…» и «Дуров поделился,
что ему не присылают шпионок»). Общих слов в таких подписях два-три, и
текстовый порог, который их склеил бы, склеивал бы любые посты на одну тему.

Метод: перцептивный хэш картинки (core/image_hash.py) сравнивается с хэшами
картинок постов за lookback_hours. Дубль, если расстояние Хэмминга не больше
max_distance и выполнены обе защиты:

* **Другой источник.** Канал с одной заставкой на всех постах иначе
  блокировал бы сам себя.
* **Тексты не о разном.** Если в обоих постах есть текст (не меньше
  min_text_stems основ), у них должно быть хотя бы min_shared_stems общих
  основ: одно стоковое фото не делает разные новости одной. Если текста
  почти нет (мем с подписью канала, пост без текста), решает картинка.

Картинку скачивает адаптер источника (IImageHasher): только фото и первое
фото альбома, без превью ссылок (там часто общая картинка сайта). Скачивание
стоит одного запроса, поэтому слой стоит ставить последним: до него дойдут
только посты, которые текстовые слои пропустили.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from news_aggregator.core.image_hash import HASH_SIZE, hamming_distance
from news_aggregator.core.interfaces import IDeduplicator, IImageHasher, IImageHashIndex
from news_aggregator.core.lemmatize import extract_lemma_set, stem_set
from news_aggregator.core.models import DeduplicationResult, ImageHashRecord, ProcessedMessage

logger = logging.getLogger(__name__)

# Хэш, посчитанный в check(), нужен ещё в remember() того же сообщения:
# второе скачивание было бы лишним запросом к Telegram.
_MAX_CACHED_HASHES = 256


class ImageDeduplicator(IDeduplicator):
    """Считает сообщение дублем, если похожая картинка недавно вышла из
    другого источника (см. описание модуля)."""

    def __init__(
        self,
        index: IImageHashIndex,
        hasher: IImageHasher | None,
        max_distance: int = 20,
        lookback_hours: float = 24.0,
        min_text_stems: int = 4,
        min_shared_stems: int = 2,
        stem_length: int | None = 5,
    ) -> None:
        if not (0 <= max_distance < HASH_SIZE * HASH_SIZE):
            raise ValueError(f"max_distance должен быть в диапазоне 0..{HASH_SIZE**2 - 1}")
        if lookback_hours <= 0:
            raise ValueError("lookback_hours должен быть положительным")
        if min_text_stems < 1:
            raise ValueError("min_text_stems должен быть положительным")
        if min_shared_stems < 1:
            raise ValueError("min_shared_stems должен быть положительным")
        if stem_length is not None and stem_length < 2:
            raise ValueError("stem_length должен быть >= 2 или null (без усечения)")
        self._index = index
        self._hasher = hasher
        self._max_distance = max_distance
        self._lookback_hours = lookback_hours
        self._min_text_stems = min_text_stems
        self._min_shared = min_shared_stems
        self._stem_length = stem_length
        self._hashes: dict[tuple[str, str], int | None] = {}

    @property
    def name(self) -> str:
        return "image_deduplicator"

    def _since(self) -> datetime:
        return datetime.now(UTC) - timedelta(hours=self._lookback_hours)

    def _stems(self, message: ProcessedMessage) -> frozenset[str]:
        return stem_set(extract_lemma_set(message.normalized_text), self._stem_length)

    async def _hash(self, message: ProcessedMessage) -> int | None:
        if self._hasher is None or message.raw.media is None:
            return None
        key = (message.raw.source_id, message.raw.external_id)
        if key not in self._hashes:
            if len(self._hashes) >= _MAX_CACHED_HASHES:
                self._hashes.clear()
            self._hashes[key] = await self._hasher.hash(message.raw.media)
        return self._hashes[key]

    def _texts_conflict(self, stems: frozenset[str], candidate: frozenset[str]) -> str | None:
        """Причина не считать дублем, если оба текста есть и они о разном."""
        if len(stems) < self._min_text_stems or len(candidate) < self._min_text_stems:
            return None
        shared = len(stems & candidate)
        if shared < self._min_shared:
            return f"картинка похожа, но общих основ в тексте {shared} < {self._min_shared}"
        return None

    async def check(self, message: ProcessedMessage) -> DeduplicationResult:
        if self._hasher is None:
            return DeduplicationResult(
                is_duplicate=False, reason="картинки скачивать нечем (нет Telegram-клиента)"
            )
        image_hash = await self._hash(message)
        if image_hash is None:
            return DeduplicationResult(is_duplicate=False, reason="картинки нет")

        stems = self._stems(message)
        best: int | None = None
        rejected: str | None = None
        for candidate in await self._index.find_recent(self._since()):
            if candidate.source_id == message.raw.source_id:
                continue
            distance = hamming_distance(image_hash, candidate.image_hash)
            if best is None or distance < best:
                best = distance
            if distance > self._max_distance:
                continue
            conflict = self._texts_conflict(stems, candidate.stems)
            if conflict is not None:
                rejected = conflict
                continue
            return DeduplicationResult(
                is_duplicate=True,
                reason=(
                    f"та же картинка уже выходила из '{candidate.source_id}' "
                    f"(расстояние {distance} <= {self._max_distance}, "
                    f"окно={self._lookback_hours}ч)"
                ),
            )

        if rejected is not None:
            return DeduplicationResult(is_duplicate=False, reason=rejected)
        detail = f", ближайшая на расстоянии {best}" if best is not None else ""
        return DeduplicationResult(
            is_duplicate=False, reason=f"похожих картинок из других каналов нет{detail}"
        )

    async def remember(self, message: ProcessedMessage) -> None:
        image_hash = await self._hash(message)
        self._hashes.pop((message.raw.source_id, message.raw.external_id), None)
        if image_hash is None:
            return
        await self._index.save(
            ImageHashRecord(
                image_hash=image_hash,
                source_id=message.raw.source_id,
                stems=self._stems(message),
            ),
            external_id=message.raw.external_id,
        )
        deleted = await self._index.purge_older_than(self._since())
        if deleted:
            logger.debug(
                "Удалено %d устаревших отпечатков картинок (старше %sч)",
                deleted,
                self._lookback_hours,
            )
