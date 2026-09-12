"""Фильтр, отсеивающий рекламные посты по ключевым словам.

Проверка ведётся по нормализованному тексту, поэтому регистр и пунктуация
ключевых слов в конфигурации не важны.

Ограничение: сравнение идёт по точной подстроке нормализованного текста,
без учёта словоизменения (например, ключевое слово "скидка" не совпадёт
со словоформой "скидкой"). Для более точного покрытия склонений в
config.yaml можно перечислить несколько словоформ одного ключевого слова.
"""

from __future__ import annotations

from collections.abc import Sequence

from news_aggregator.core.interfaces import IFilter
from news_aggregator.core.models import ProcessedMessage
from news_aggregator.core.text_utils import normalize_text

_DEFAULT_KEYWORDS: tuple[str, ...] = (
    "реклама",
    "промокод",
    "скидка",
    "подпишись и выиграй",
)


class AdFilter(IFilter):
    """Отбрасывает сообщения, содержащие рекламные ключевые слова."""

    def __init__(self, keywords: Sequence[str] | None = None) -> None:
        source_keywords = keywords if keywords is not None else _DEFAULT_KEYWORDS
        self._keywords = tuple(normalize_text(k) for k in source_keywords if k)

    @property
    def name(self) -> str:
        return "ad_filter"

    def should_drop(self, message: ProcessedMessage) -> tuple[bool, str | None]:
        for keyword in self._keywords:
            if keyword and keyword in message.normalized_text:
                return True, f"обнаружено рекламное ключевое слово: '{keyword}'"
        return False, None
