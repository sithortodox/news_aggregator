"""Дедупликатор по контент-ному хэшу файла: один и тот же файл — дубль.

Ловит то, что текстовые слои не могут, когда у постов одинаковое медиа,
но разные тексты: рекламный пост из источника А ушёл в источник B с другим
подписом (файл тот же, пересказ/адаптация другого канала), либо один файл
опубликовали два разных канала.

Метод: SHA-256 контента файла (core/file_hash.py) сравнивается с хэшами
файлов постов за lookback_hours. Дубль, если хэши совпали и выполнены обе
защиты:

* **Другой источник.** Канал с одной и той же заставкой на всех постах
  иначе блокировал бы сам себя.
* **Тексты не о разном.** Если в обоих постах есть текст (не меньше
  min_text_stems основ), должно быть хотя бы min_shared_stems общих основ —
  один и тот же файл может обсуждали разным составом событий.

Text-hash-дедупликатор сравнивает `kind:file_id` fingerprint медиа, что ловит
только один и тот же файл внутри Telegram; content-хэш ловит переупаковки и
переотправки с другими file_id — как раз случай «разные тексты, одинаковое
медиа».
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from news_aggregator.core.interfaces import IDeduplicator, IFileHasher, IFileHashIndex
from news_aggregator.core.lemmatize import extract_lemma_set, stem_set
from news_aggregator.core.models import DeduplicationResult, FileHashRecord, ProcessedMessage

logger = logging.getLogger(__name__)

_MAX_CACHED_HASHES = 256


class FileHashDeduplicator(IDeduplicator):
    """Считает сообщение дублем, если контентный SHA-256 файла уже выходил из
    другого источника (см. описание модуля)."""

    def __init__(
        self,
        index: IFileHashIndex,
        hasher: IFileHasher | None,
        lookback_hours: float = 24.0,
        min_text_stems: int = 4,
        min_shared_stems: int = 2,
        stem_length: int | None = 5,
    ) -> None:
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
        self._lookback_hours = lookback_hours
        self._min_text_stems = min_text_stems
        self._min_shared = min_shared_stems
        self._stem_length = stem_length
        self._hashes: dict[tuple[str, str], str | None] = {}

    @property
    def name(self) -> str:
        return "file_hash_deduplicator"

    def _since(self) -> datetime:
        return datetime.now(UTC) - timedelta(hours=self._lookback_hours)

    def _stems(self, message: ProcessedMessage) -> frozenset[str]:
        return stem_set(extract_lemma_set(message.normalized_text), self._stem_length)

    async def _hash(self, message: ProcessedMessage) -> str | None:
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
            return f"файл совпадает, но общих основ в тексте {shared} < {self._min_shared}"
        return None

    async def check(self, message: ProcessedMessage) -> DeduplicationResult:
        file_hash = await self._hash(message)
        if file_hash is None:
            return DeduplicationResult(is_duplicate=False, reason="файлы нет")

        stems = self._stems(message)
        rejected: str | None = None
        for candidate in await self._index.find_recent(self._since()):
            if candidate.source_id == message.raw.source_id:
                continue
            if candidate.file_hash != file_hash:
                continue
            conflict = self._texts_conflict(stems, candidate.stems)
            if conflict is not None:
                rejected = conflict
                continue
            return DeduplicationResult(
                is_duplicate=True,
                reason=(
                    f"тот же файл уже выходил из '{candidate.source_id}' "
                    f"(sha256 совпадает, окно={self._lookback_hours}ч)"
                ),
            )

        if rejected is not None:
            return DeduplicationResult(is_duplicate=False, reason=rejected)
        return DeduplicationResult(
            is_duplicate=False,
            reason="совпадающих файлов из других источников нет",
        )

    async def remember(self, message: ProcessedMessage) -> None:
        file_hash = await self._hash(message)
        self._hashes.pop((message.raw.source_id, message.raw.external_id), None)
        if file_hash is None:
            return
        await self._index.save(
            FileHashRecord(
                file_hash=file_hash,
                source_id=message.raw.source_id,
                stems=self._stems(message),
            ),
            external_id=message.raw.external_id,
        )
        deleted = await self._index.purge_older_than(self._since())
        if deleted:
            logger.debug(
                "Удалено %d устаревших контент-хэшей файлов (старше %sч)",
                deleted,
                self._lookback_hours,
            )
