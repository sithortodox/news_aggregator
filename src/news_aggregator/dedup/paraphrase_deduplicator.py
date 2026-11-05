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

Три защиты от ошибок обоих знаков (подобраны на корпусе в
tests/support/dedup_corpus.py: 60 из 69 дублей вместо 53 при 2 ложных
срабатываниях вместо 4 на 2277 парах разных событий):

* **Основы** (stem_length): леммы усекаются до N символов перед сравнением —
  склеивает «Канада»/«канадцы», «хоккей»/«хоккейный», «биткоин»/«биткоина».
* **Минимум общих основ** (min_shared_lemmas): два общих слова из пяти
  («ключевая ставка») — не повод считать разные новости одной, как бы ни
  выглядел Жаккар на таком коротком тексте.
* **Вето по числам** (number_veto): если в обоих текстах есть числа и ни одно не
  совпадает («ставка 18%» и «ставка 16%»), это разные события или разные
  данные, а не пересказ. Числа берутся из множества лемм: у кандидатов из
  индекса другого источника нет.

Опционально (min_overlap, по умолчанию выключено): дубль также, если доля
основ меньшего текста, найденных в большем, не ниже порога. Ловит «заголовок
против полного текста», где Жаккар занижен длиной.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from news_aggregator.core.interfaces import IDeduplicator, ILemmaSetIndex
from news_aggregator.core.lemmatize import extract_lemma_set, numbers_in, stem_set
from news_aggregator.core.models import DeduplicationResult, ProcessedMessage

logger = logging.getLogger(__name__)


class ParaphraseDeduplicator(IDeduplicator):
    """Считает сообщение дублем, если в окне lookback_hours нашёлся другой
    текст, похожий по основам лемм (см. описание модуля)."""

    def __init__(
        self,
        index: ILemmaSetIndex,
        min_jaccard: float = 0.17,
        lookback_hours: float = 24.0,
        min_lemmas: int = 4,
        stem_length: int | None = 5,
        min_shared_lemmas: int = 3,
        number_veto: bool = True,
        min_overlap: float | None = None,
    ) -> None:
        if not (0.0 < min_jaccard <= 1.0):
            raise ValueError("min_jaccard должен быть в диапазоне (0.0, 1.0]")
        if lookback_hours <= 0:
            raise ValueError("lookback_hours должен быть положительным")
        if min_lemmas < 1:
            raise ValueError("min_lemmas должен быть положительным")
        if stem_length is not None and stem_length < 2:
            raise ValueError("stem_length должен быть >= 2 или null (без усечения)")
        if min_shared_lemmas < 1:
            raise ValueError("min_shared_lemmas должен быть положительным")
        if min_overlap is not None and not (0.0 < min_overlap <= 1.0):
            raise ValueError("min_overlap должен быть в диапазоне (0.0, 1.0] или null")
        self._index = index
        self._min_jaccard = min_jaccard
        self._lookback_hours = lookback_hours
        self._min_lemmas = min_lemmas
        self._stem_length = stem_length
        self._min_shared = min_shared_lemmas
        self._number_veto = number_veto
        self._min_overlap = min_overlap

    def _compare(
        self, lemmas: frozenset[str], candidate: frozenset[str]
    ) -> tuple[bool, str, float]:
        """Сравнивает два множества лемм. Возвращает (дубль?, объяснение, оценка)."""
        a = stem_set(lemmas, self._stem_length)
        b = stem_set(candidate, self._stem_length)
        shared = len(a & b)
        union = len(a | b)
        jaccard = shared / union if union else 0.0

        if self._number_veto:
            numbers_a, numbers_b = numbers_in(lemmas), numbers_in(candidate)
            if numbers_a and numbers_b and not (numbers_a & numbers_b):
                return False, "числа в текстах не совпадают", jaccard

        if shared < self._min_shared:
            return False, f"общих основ {shared} < {self._min_shared}", jaccard

        if jaccard >= self._min_jaccard:
            return (
                True,
                f"Жаккар по основам={jaccard:.2f} >= {self._min_jaccard}, общих основ={shared}",
                jaccard,
            )
        smaller = min(len(a), len(b))
        if self._min_overlap is not None and smaller and shared / smaller >= self._min_overlap:
            return (
                True,
                f"доля общих основ меньшего текста={shared / smaller:.2f} "
                f">= {self._min_overlap}, общих основ={shared}",
                jaccard,
            )
        return False, f"Жаккар {jaccard:.2f} < {self._min_jaccard}", jaccard

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
            is_duplicate, explanation, score = self._compare(lemmas, candidate)
            if best_score is None or score > best_score:
                best_score = score
            if is_duplicate:
                return DeduplicationResult(
                    is_duplicate=True,
                    reason=(
                        f"похоже на пересказ той же новости ({explanation}, "
                        f"окно={self._lookback_hours}ч)"
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
