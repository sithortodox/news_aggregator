"""Фильтр, отсеивающий пустые и слишком короткие сообщения."""

from __future__ import annotations

from news_aggregator.core.interfaces import IFilter
from news_aggregator.core.models import ProcessedMessage


class LengthFilter(IFilter):
    """Отбрасывает сообщения, чей нормализованный текст короче min_length."""

    def __init__(self, min_length: int = 20) -> None:
        if min_length < 0:
            raise ValueError("min_length не может быть отрицательным")
        self._min_length = min_length

    @property
    def name(self) -> str:
        return "length_filter"

    def should_drop(self, message: ProcessedMessage) -> tuple[bool, str | None]:
        length = len(message.normalized_text)
        if length == 0:
            return True, "пустой текст после нормализации"
        if length < self._min_length:
            return True, f"текст короче {self._min_length} символов (длина: {length})"
        return False, None
