"""Общий для фильтров способ собрать все тексты поста, где может быть реклама."""

from __future__ import annotations

from news_aggregator.core.links import entity_urls
from news_aggregator.core.models import ProcessedMessage


def searchable_texts(message: ProcessedMessage) -> list[str]:
    """Исходный текст поста и URL его гиперссылок."""
    return [message.raw.text, *entity_urls(message)]
