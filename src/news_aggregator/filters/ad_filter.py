"""Фильтр, отсеивающий рекламные посты по ключевым словам.

Проверка ведётся по нормализованному тексту, поэтому регистр и пунктуация
ключевых слов в конфигурации не важны.

Помимо ключевых слов, проверяются регулярные выражения (patterns) по исходному
тексту и URL гиперссылок. По умолчанию — маркировка рекламы `erid`: с 2023 года
в России любая легальная реклама в соцсетях обязана нести токен ОРД, в тексте
он выглядит как `erid: 2Vtzq...` или `?erid=2Vtzq...`. Это надёжнее списка слов:
такой токен почти не встречается вне рекламы, а слова («скидка», «промокод»)
бывают и в новостях. Значение `patterns: []` отключает проверку.

Ограничение: сравнение по ключевым словам идёт по точной подстроке нормализованного текста,
без учёта словоизменения (например, ключевое слово "скидка" не совпадёт
со словоформой "скидкой"). Для более точного покрытия склонений в
config.yaml можно перечислить несколько словоформ одного ключевого слова.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from news_aggregator.core.interfaces import IFilter
from news_aggregator.core.models import ProcessedMessage
from news_aggregator.core.text_utils import normalize_text
from news_aggregator.filters._text_sources import searchable_texts

_DEFAULT_KEYWORDS: tuple[str, ...] = (
    "реклама",
    "промокод",
    "скидка",
    "подпишись и выиграй",
)


# erid идёт не после буквы/цифры (иначе «Meridian 123456» дало бы ложное срабатывание)
# и сопровождается токеном из 6+ символов.
_DEFAULT_PATTERNS: tuple[str, ...] = (r"(?<![a-z0-9])erid\s*[:=]?\s*[a-z0-9]{6,}",)


class AdFilter(IFilter):
    """Отбрасывает сообщения с рекламными ключевыми словами или маркировкой рекламы."""

    def __init__(
        self, keywords: Sequence[str] | None = None, patterns: Sequence[str] | None = None
    ) -> None:
        source_keywords = keywords if keywords is not None else _DEFAULT_KEYWORDS
        self._keywords = tuple(normalize_text(k) for k in source_keywords if k)
        source_patterns = patterns if patterns is not None else _DEFAULT_PATTERNS
        try:
            self._patterns = tuple(re.compile(p, re.IGNORECASE) for p in source_patterns if p)
        except re.error as exc:
            raise ValueError(f"Некорректное регулярное выражение в patterns: {exc}") from exc

    @property
    def name(self) -> str:
        return "ad_filter"

    def should_drop(self, message: ProcessedMessage) -> tuple[bool, str | None]:
        for keyword in self._keywords:
            if keyword and keyword in message.normalized_text:
                return True, f"обнаружено рекламное ключевое слово: '{keyword}'"
        for text in searchable_texts(message):
            for pattern in self._patterns:
                match = pattern.search(text)
                if match:
                    return True, f"обнаружена рекламная маркировка: '{match.group(0)[:40]}'"
        return False, None
