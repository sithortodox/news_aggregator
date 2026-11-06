"""Фильтр, отсеивающий пустые и слишком короткие сообщения."""

from __future__ import annotations

from news_aggregator.core.interfaces import IFilter
from news_aggregator.core.models import ProcessedMessage


class LengthFilter(IFilter):
    """Отбрасывает сообщения, чей нормализованный текст короче min_length.

    Пост с медиа совсем без текста — не пустой: его содержание в файле. Такие
    посты проходят, если allow_media_only не выключен. Короткая подпись к медиа
    по-прежнему отсеивается: общая подпись вроде «Фото дня» у разных файлов
    сделала бы эти посты дублями по тексту.
    """

    def __init__(self, min_length: int = 20, allow_media_only: bool = True) -> None:
        if min_length < 0:
            raise ValueError("min_length не может быть отрицательным")
        self._min_length = min_length
        self._allow_media_only = allow_media_only

    @property
    def name(self) -> str:
        return "length_filter"

    def should_drop(self, message: ProcessedMessage) -> tuple[bool, str | None]:
        length = len(message.normalized_text)
        if length == 0:
            if self._allow_media_only and message.raw.media is not None:
                return False, None
            return True, "пустой текст после нормализации"
        if length < self._min_length:
            return True, f"текст короче {self._min_length} символов (длина: {length})"
        return False, None
