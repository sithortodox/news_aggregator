"""Дедупликатор, ловящий одну и ту же новость, пересказанную разными
словами в разных каналах — то, что TextHashDeduplicator и
SimHashDeduplicator принципиально не могут поймать (см. их docstring).

Метод: множество лемм (словарных форм слов, см. core/lemmatize.py)
нормализованного текста сравнивается с множествами лемм недавних
сообщений через коэффициент Жаккара (|A∩B| / |A∪B|). Лемматизация решает
главную проблему сравнения русских пересказов "в лоб" (по словам или
n-граммам без приведения к словарной форме): падежи и спряжения меняют
само слово ("ставку" vs "ставки", "открылась" vs "открытии"), из-за чего
разные пересказы одной новости почти не имеют общих словоформ, но почти
всегда имеют много общих ЛЕММ.

Это по-прежнему не полноценная семантическая дедупликация (два текста
без единого общего значимого слова, но об одном и том же, всё ещё не
поймать) — но существенно лучше, чем ничего, для типичного случая, когда
разные каналы пересказывают одну новость похожими словами и терминами.

Короткие тексты (после лемматизации и удаления стоп-слов остаётся мало
токенов) намеренно не участвуют в сравнении: на малом числе токенов
коэффициент Жаккара становится случайным и даёт много ложных срабатываний.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from news_aggregator.core.interfaces import IDeduplicator, ILemmaSetIndex
from news_aggregator.core.lemmatize import extract_lemma_set, jaccard_similarity
from news_aggregator.core.models import DeduplicationResult, ProcessedMessage

logger = logging.getLogger(__name__)


class ParaphraseDeduplicator(IDeduplicator):
    """Считает сообщение дублем, если в окне lookback_hours нашёлся другой
    текст с коэффициентом Жаккара по леммам >= min_jaccard."""

    def __init__(
        self,
        index: ILemmaSetIndex,
        min_jaccard: float = 0.17,
        lookback_hours: float = 24.0,
        min_lemmas: int = 4,
    ) -> None:
        if not (0.0 < min_jaccard <= 1.0):
            raise ValueError("min_jaccard должен быть в диапазоне (0.0, 1.0]")
        if lookback_hours <= 0:
            raise ValueError("lookback_hours должен быть положительным")
        if min_lemmas < 1:
            raise ValueError("min_lemmas должен быть положительным")
        self._index = index
        self._min_jaccard = min_jaccard
        self._lookback_hours = lookback_hours
        self._min_lemmas = min_lemmas

    @property
    def name(self) -> str:
        return "paraphrase_deduplicator"

    def _since(self) -> datetime:
        return datetime.now(UTC) - timedelta(hours=self._lookback_hours)

    async def check(self, message: ProcessedMessage) -> DeduplicationResult:
        lemmas = extract_lemma_set(message.normalized_text)
        if len(lemmas) < self._min_lemmas:
            return DeduplicationResult(
                is_duplicate=False,
                reason=(
                    f"слишком мало значимых слов ({len(lemmas)} < {self._min_lemmas}) "
                    "для надёжного сравнения по леммам"
                ),
            )

        candidates = await self._index.find_recent(self._since())

        best_score: float | None = None
        for candidate in candidates:
            score = jaccard_similarity(lemmas, candidate)
            if best_score is None or score > best_score:
                best_score = score
            if score >= self._min_jaccard:
                return DeduplicationResult(
                    is_duplicate=True,
                    reason=(
                        f"похоже на пересказ той же новости (Жаккар по леммам={score:.2f} "
                        f">= {self._min_jaccard}, окно={self._lookback_hours}ч)"
                    ),
                )

        detail = ""
        if best_score is not None:
            detail = f", ближайший кандидат: {best_score:.2f}"
        return DeduplicationResult(
            is_duplicate=False, reason=f"похожих по смыслу сообщений не найдено{detail}"
        )

    async def remember(self, message: ProcessedMessage) -> None:
        lemmas = extract_lemma_set(message.normalized_text)
        if not lemmas:
            return
        await self._index.save(
            lemmas, source_id=message.raw.source_id, external_id=message.raw.external_id
        )
        deleted = await self._index.purge_older_than(self._since())
        if deleted:
            logger.debug(
                "Удалено %d устаревших множеств лемм (старше %sч)",
                deleted,
                self._lookback_hours,
            )
