"""Регистрация всех известных компонентов в ComponentRegistry.

Это единственное место в проекте, которое "знает" обо всех конкретных
реализациях сразу. Пайплайн и остальной код продолжают работать только
через интерфейсы и реестр.

Регистрация Telegram-компонентов обёрнута в try/except ImportError: если
пакет telethon не установлен, приложение всё равно может запускаться в
режиме fake-источника (MVP без Telegram), просто telegram_* компоненты
будут недоступны и это будет явно залогировано.
"""

from __future__ import annotations

import logging

from news_aggregator.core.registry import ComponentRegistry
from news_aggregator.dedup.hash_deduplicator import TextHashDeduplicator
from news_aggregator.dedup.link_deduplicator import LinkDeduplicator
from news_aggregator.filters.ad_filter import AdFilter
from news_aggregator.filters.length_filter import LengthFilter
from news_aggregator.publishers.console_publisher import ConsolePublisher
from news_aggregator.sources.fake_source import FakeSourceReader
from news_aggregator.storage.sqlite_source_repository import SqliteSourceRepository
from news_aggregator.storage.sqlite_storage import SqliteStorage

logger = logging.getLogger(__name__)


def build_registry() -> ComponentRegistry:
    """Создаёт и наполняет ComponentRegistry всеми известными компонентами."""
    registry = ComponentRegistry()

    # Фильтры
    registry.filters.register("length_filter", LengthFilter)
    registry.filters.register("ad_filter", AdFilter)

    # Дедупликаторы (принимают storage - подставляется в main.py при создании)
    registry.deduplicators.register("text_hash_deduplicator", TextHashDeduplicator)
    registry.deduplicators.register("link_deduplicator", LinkDeduplicator)

    # Обогатители: пока нет ни одного встроенного (Stage вне рамок MVP),
    # но категория уже готова к расширению без изменения пайплайна.

    # Издатели
    registry.publishers.register("console_publisher", ConsolePublisher)

    # Источники
    registry.sources.register("fake", FakeSourceReader)

    # Хранилища
    registry.storages.register("sqlite", SqliteStorage)

    # Репозиторий списка источников (используется и пайплайном, и ботом
    # управления каналами — см. news_aggregator.bot)
    registry.source_repositories.register("sqlite", SqliteSourceRepository)

    _try_register_telegram(registry)

    return registry


def _try_register_telegram(registry: ComponentRegistry) -> None:
    """Регистрирует Telegram-компоненты, если установлен пакет telethon."""
    try:
        from news_aggregator.publishers.telegram_publisher import TelegramPublisher
        from news_aggregator.sources.telegram_source import TelegramSourceReader
    except ImportError:
        logger.warning(
            "Пакет telethon не установлен: telegram_source/telegram_publisher "
            "недоступны. MVP продолжит работать с источником 'fake'."
        )
        return

    registry.sources.register("telegram", TelegramSourceReader)
    registry.publishers.register("telegram_publisher", TelegramPublisher)
