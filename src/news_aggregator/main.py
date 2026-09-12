"""Точка входа приложения: CLI, сборка компонентов, запуск пайплайна.

Использование:
    python -m news_aggregator.main --config config/config.yaml --once
    python -m news_aggregator.main --config config/config.yaml
    news-aggregator --once  (после `pip install -e .`)

Флаг --once выполняет один проход и завершается (удобно для cron/systemd
timer). Без --once процесс работает в цикле с интервалом из
pipeline.poll_interval_seconds, пока не будет остановлен (Ctrl+C).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import TYPE_CHECKING

from news_aggregator.bootstrap import build_registry
from news_aggregator.config.loader import (
    ConfigError,
    TelegramSecrets,
    load_app_config,
    load_env_secrets,
)
from news_aggregator.config.schema import AppConfig, ComponentConfig
from news_aggregator.core.interfaces import (
    IDeduplicator,
    IEnricher,
    IFilter,
    IPublisher,
    ISourceReader,
    ISourceRepository,
    IStorage,
)
from news_aggregator.core.models import Source
from news_aggregator.core.pipeline import AggregatorPipeline
from news_aggregator.core.registry import ComponentNotRegisteredError, ComponentRegistry

if TYPE_CHECKING:
    from news_aggregator.bot.telegram_bot import AggregatorBot as AggregatorBotType

logger = logging.getLogger("news_aggregator")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def _build_storage(registry: ComponentRegistry, config: AppConfig) -> IStorage:
    return registry.storages.create(config.storage.name, **config.storage.params)  # type: ignore[return-value]


def _build_filters(registry: ComponentRegistry, config: AppConfig) -> list[IFilter]:
    return [
        registry.filters.create(c.name, **c.params)  # type: ignore[misc]
        for c in config.filters
    ]


def _build_deduplicators(
    registry: ComponentRegistry, config: AppConfig, storage: IStorage
) -> list[IDeduplicator]:
    return [
        registry.deduplicators.create(c.name, storage=storage, **c.params)  # type: ignore[misc]
        for c in config.deduplicators
    ]


def _build_readers(registry: ComponentRegistry) -> list[ISourceReader]:
    """Строит все доступные ридеры, независимо от того, какие источники
    сейчас в списке — список источников теперь динамический (см.
    ISourceRepository), канал может быть добавлен позже через бота, и
    ридер для его kind должен уже быть готов."""
    readers: list[ISourceReader] = [registry.sources.create("fake")]  # type: ignore[list-item]

    if "telegram" not in registry.sources.available():
        logger.info(
            "Пакет telethon не установлен: Telegram-каналы читаться не будут "
            "(источник 'fake' по-прежнему работает)."
        )
        return readers

    secrets = load_env_secrets()
    if secrets.api_id is None or secrets.api_hash is None:
        logger.info(
            "TELEGRAM_API_ID/TELEGRAM_API_HASH не заданы в .env: Telegram-каналы "
            "читаться не будут, пока секреты не будут добавлены."
        )
        return readers

    from news_aggregator.sources.telegram_client import build_telegram_client

    client = build_telegram_client(secrets)
    readers.append(registry.sources.create("telegram", client=client))  # type: ignore[arg-type]
    return readers


def _build_publishers(registry: ComponentRegistry, config: AppConfig) -> list[IPublisher]:
    publishers: list[IPublisher] = []
    for c in config.publishers:
        if c.name == "telegram_publisher":
            publishers.append(_build_telegram_publisher(registry, c))
        else:
            publishers.append(registry.publishers.create(c.name, **c.params))  # type: ignore[arg-type]
    return publishers


def _build_telegram_publisher(
    registry: ComponentRegistry, component_config: ComponentConfig
) -> IPublisher:
    try:
        from news_aggregator.sources.telegram_client import build_telegram_client
    except ImportError as exc:
        raise RuntimeError(
            "В конфигурации указан publisher 'telegram_publisher', но пакет telethon не установлен."
        ) from exc

    secrets = load_env_secrets()
    client = build_telegram_client(secrets)
    target_channel = component_config.params.get("target_channel") or secrets.target_channel
    if not target_channel:
        raise RuntimeError(
            "Не задан целевой канал для telegram_publisher: укажите "
            "TELEGRAM_TARGET_CHANNEL в .env или params.target_channel в config.yaml"
        )
    return registry.publishers.create(  # type: ignore[return-value]
        "telegram_publisher", client=client, target_channel=target_channel
    )


def _source_repository_db_path(config: AppConfig) -> str:
    """Путь к БД репозитория источников.

    По умолчанию — тот же файл, что и у основного хранилища (это просто
    ещё одна таблица в той же SQLite-базе), чтобы не плодить отдельную
    секцию конфигурации ради одного пути. Если storage не sqlite или путь
    не задан, используется тот же дефолт, что и у SqliteStorage.
    """
    if config.storage.name == "sqlite":
        db_path = config.storage.params.get("db_path")
        if isinstance(db_path, str):
            return db_path
    return "data/state.db"


async def _seed_sources_from_config(
    repository: ISourceRepository, sources: tuple[Source, ...]
) -> None:
    """Добавляет в репозиторий источники, объявленные в config.yaml, если
    их там ещё нет (сравнение по identifier). Идемпотентно: не трогает
    источники, уже добавленные ранее (в т.ч. через бота) — так что
    /remove в боте не будет "отменяться" перезапуском, если сам YAML не
    менялся."""
    existing = await repository.list_sources()
    existing_identifiers = {s.identifier for s in existing}
    for source in sources:
        if source.identifier not in existing_identifiers:
            await repository.add_source(source)


async def build_pipeline(
    config: AppConfig, registry: ComponentRegistry
) -> tuple[AggregatorPipeline, ISourceRepository]:
    """Собирает AggregatorPipeline из конфигурации, используя реестр компонентов.

    Возвращает также ISourceRepository — тот же экземпляр, что использует
    пайплайн для получения списка источников на каждый прогон. main()
    передаёт его же в бота управления каналами, чтобы /add и /remove сразу
    были видны следующему run_once() без пересоздания пайплайна.
    """
    storage = _build_storage(registry, config)
    filters = _build_filters(registry, config)
    deduplicators = _build_deduplicators(registry, config, storage)
    enrichers: list[IEnricher] = [
        registry.enrichers.create(c.name, **c.params)  # type: ignore[misc]
        for c in config.enrichers
    ]
    publishers = _build_publishers(registry, config)
    readers = _build_readers(registry)

    source_repository: ISourceRepository = registry.source_repositories.create(  # type: ignore[assignment]
        "sqlite", db_path=_source_repository_db_path(config)
    )
    await _seed_sources_from_config(source_repository, config.sources)

    for source in await source_repository.list_sources():
        if source.enabled and not any(r.supports(source) for r in readers):
            logger.warning(
                "Для источника '%s' (kind=%s) нет доступного ридера — "
                "проверьте, установлен ли telethon и заданы ли "
                "TELEGRAM_API_ID/TELEGRAM_API_HASH в .env",
                source.id,
                source.kind.value,
            )

    pipeline = AggregatorPipeline(
        source_repository=source_repository,
        readers=readers,
        filters=filters,
        deduplicators=deduplicators,
        enrichers=enrichers,
        publishers=publishers,
        storage=storage,
        max_messages_per_source=config.pipeline.max_messages_per_source,
        dry_run=config.pipeline.dry_run,
    )
    return pipeline, source_repository


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="news-aggregator",
        description="Персональный агрегатор новостей из Telegram",
    )
    parser.add_argument(
        "--config",
        default="config/config.yaml",
        help="Путь к YAML-конфигурации (по умолчанию: config/config.yaml)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Выполнить один проход по источникам и завершиться",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help=(
            "Проверить config.yaml и .env (собрать пайплайн, но не запускать его) "
            "и завершиться. Полезно перед перезапуском сервиса на VPS после "
            "правки конфигурации — не трогает сеть/Telegram."
        ),
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Переопределить интервал опроса в секундах (иначе берётся из config.yaml)",
    )
    dry_run_group = parser.add_mutually_exclusive_group()
    dry_run_group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=None,
        help="Принудительно включить dry-run (ничего реально не публиковать)",
    )
    dry_run_group.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        default=None,
        help="Принудительно выключить dry-run (публиковать по-настоящему)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Подробное логирование (DEBUG)"
    )
    return parser.parse_args(argv)


def _print_validation_summary(config: AppConfig, sources: list[Source]) -> None:
    """Печатает человекочитаемую сводку по успешно собранной конфигурации.

    sources — фактический список из ISourceRepository (config.yaml + всё,
    что ранее было добавлено через бота), а не только то, что записано в
    самом YAML.
    """
    print("Конфигурация корректна.\n")
    print(f"Режим: {'dry-run' if config.pipeline.dry_run else 'РЕАЛЬНАЯ ПУБЛИКАЦИЯ'}")
    print(f"Интервал опроса: {config.pipeline.poll_interval_seconds} сек.")
    print(f"Хранилище: {config.storage.name} {config.storage.params or ''}".rstrip())

    print(f"\nИсточники ({len(sources)}):")
    for source in sources:
        status = "включён" if source.enabled else "ВЫКЛЮЧЕН"
        print(f"  - {source.id}: {source.kind.value} ({source.identifier}) [{status}]")

    for label, components in (
        ("Фильтры", config.filters),
        ("Дедупликаторы", config.deduplicators),
        ("Обогатители", config.enrichers),
        ("Издатели", config.publishers),
    ):
        names = ", ".join(c.name for c in components) or "(нет)"
        print(f"{label}: {names}")


def _try_build_bot(
    secrets: TelegramSecrets, source_repository: ISourceRepository
) -> AggregatorBotType | None:
    """Пытается собрать бота управления каналами.

    Возвращает None (и логирует причину), если бот не настроен или его
    зависимость не установлена — это НЕ ошибка, просто бот не стартует,
    остальной пайплайн продолжает работать как обычно.
    """
    if not secrets.bot_token:
        return None

    if not secrets.bot_owner_id:
        logger.error(
            "TELEGRAM_BOT_TOKEN задан, но TELEGRAM_BOT_OWNER_ID — нет. "
            "Бот управления каналами не будет запущен (напишите @userinfobot, "
            "чтобы узнать свой id, и укажите его в .env)."
        )
        return None

    try:
        from news_aggregator.bot.telegram_bot import AggregatorBot
    except ImportError:
        logger.warning(
            "TELEGRAM_BOT_TOKEN задан, но пакет python-telegram-bot не "
            "установлен: бот управления каналами не будет запущен."
        )
        return None

    return AggregatorBot(
        token=secrets.bot_token,
        owner_id=secrets.bot_owner_id,
        repository=source_repository,
    )


async def _run_forever(
    pipeline: AggregatorPipeline,
    poll_interval_seconds: int,
    bot: AggregatorBotType | None,
) -> None:
    """Цикл постоянного опроса источников, опционально вместе с ботом
    управления каналами (оба крутятся в одном event loop)."""
    stop_event = asyncio.Event()
    bot_task = asyncio.create_task(bot.run(stop_event)) if bot is not None else None

    try:
        while True:
            await pipeline.run_once()
            await asyncio.sleep(poll_interval_seconds)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Остановлено пользователем")
    finally:
        if bot_task is not None:
            stop_event.set()
            await bot_task


async def _async_main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose)

    try:
        config = load_app_config(args.config)
    except ConfigError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        return 1

    if args.dry_run is not None or args.interval is not None:
        from dataclasses import replace

        config = replace(
            config,
            pipeline=replace(
                config.pipeline,
                dry_run=args.dry_run if args.dry_run is not None else config.pipeline.dry_run,
                poll_interval_seconds=(
                    args.interval
                    if args.interval is not None
                    else config.pipeline.poll_interval_seconds
                ),
            ),
        )

    registry = build_registry()

    try:
        pipeline, source_repository = await build_pipeline(config, registry)
    except (ComponentNotRegisteredError, RuntimeError) as exc:
        logger.error("Не удалось собрать пайплайн: %s", exc)
        return 1

    if args.validate_config:
        _print_validation_summary(config, await source_repository.list_sources())
        return 0

    active_sources = [s for s in await source_repository.list_sources() if s.enabled]
    logger.info(
        "Запуск: dry_run=%s, источников=%d, once=%s",
        config.pipeline.dry_run,
        len(active_sources),
        args.once,
    )

    if args.once:
        secrets = load_env_secrets()
        if secrets.bot_token:
            logger.info(
                "TELEGRAM_BOT_TOKEN задан, но при --once бот не запускается "
                "(он имеет смысл только в режиме постоянной работы)."
            )
        await pipeline.run_once()
        return 0

    secrets = load_env_secrets()
    bot = _try_build_bot(secrets, source_repository)
    await _run_forever(pipeline, config.pipeline.poll_interval_seconds, bot)
    return 0


def run() -> None:
    """Синхронная обёртка для entry_point в pyproject.toml."""
    exit_code = asyncio.run(_async_main())
    sys.exit(exit_code)


if __name__ == "__main__":
    run()
