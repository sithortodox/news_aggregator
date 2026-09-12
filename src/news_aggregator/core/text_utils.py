"""Чистые функции для нормализации текста, извлечения ссылок и хэширования.

Эти функции не имеют побочных эффектов и не зависят ни от каких внешних
сервисов, поэтому легко тестируются и переиспользуются как в основном
пайплайне, так и в фильтрах/дедупликаторах.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Ссылки: http(s)://..., www...., t.me/...
_URL_RE = re.compile(
    r"""(?xi)
    \b(
        (?:https?://|www\.)[^\s<>"'()]+
        |
        t\.me/[^\s<>"'()]+
    )
    """
)

# Символы пунктуации/эмодзи и т.п., которые убираем при нормализации,
# но сохраняем буквы и цифры всех языков (включая кириллицу) и пробелы.
_NON_WORD_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_MULTI_SPACE_RE = re.compile(r"\s+")


def extract_links(text: str) -> tuple[str, ...]:
    """Извлекает все ссылки (http/https/www/t.me) из текста.

    Ссылки возвращаются в порядке появления, без дублей, с сохранением
    исходного регистра (в отличие от нормализованного текста).
    """
    seen: set[str] = set()
    result: list[str] = []
    for match in _URL_RE.finditer(text):
        link = match.group(1).rstrip(".,!?;:)]}")
        if link not in seen:
            seen.add(link)
            result.append(link)
    return tuple(result)


def normalize_text(text: str) -> str:
    """Приводит текст к каноническому виду для сравнения на дублирование.

    Шаги нормализации:
        1. Unicode NFKC нормализация (одинаковые по смыслу символы -> одна форма).
        2. Удаление ссылок (они дублируются отдельно и не должны влиять на
           текстовый хэш, чтобы разные подписи к одной и той же ссылке
           считались похожими).
        3. Приведение к нижнему регистру.
        4. Удаление пунктуации и эмодзи.
        5. Схлопывание повторяющихся пробелов и обрезка краёв.
    """
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _URL_RE.sub(" ", normalized)
    normalized = normalized.lower()
    normalized = _NON_WORD_RE.sub(" ", normalized)
    normalized = _MULTI_SPACE_RE.sub(" ", normalized).strip()
    return normalized


def compute_text_hash(normalized_text: str) -> str:
    """Считает sha256-хэш нормализованного текста (в hex-представлении)."""
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
