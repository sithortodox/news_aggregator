"""Общий для фильтров способ собрать все тексты поста, где может быть реклама."""

from __future__ import annotations

from news_aggregator.core.models import ProcessedMessage


def entity_urls(message: ProcessedMessage) -> list[str]:
    """URL гиперссылок на словах («Подробнее»), которых нет в самом тексте.

    Ядро не знает про Telegram, поэтому у каждого объекта форматирования
    (у Telethon — MessageEntityTextUrl) берётся строковый атрибут `url`, если он есть.
    """
    urls: list[str] = []
    for entity in message.raw.formatting_entities:
        url = getattr(entity, "url", None)
        if isinstance(url, str) and url:
            urls.append(url)
    return urls


def searchable_texts(message: ProcessedMessage) -> list[str]:
    """Исходный текст поста и URL его гиперссылок."""
    return [message.raw.text, *entity_urls(message)]
