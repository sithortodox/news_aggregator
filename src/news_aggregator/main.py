"""Точка входа приложения: CLI, сборка компонентов, запуск пайплайна.

Использование:
    python -m news_aggregator.main --config config/config.yaml --once
    python -m news_aggregator.main --config config/config.yaml
    news-aggregator --once  (после `pip install -e .`)

Флаг --once выполняет один проход и завершается (удобно для cron/systemd
timer). Без --once процесс работает в цикле с интервалом из
pipeline.poll_interval_seconds, пока не будет остановлен (Ctrl+C или
SIGTERM от `docker stop`: текущее сообщение дорабатывается, затем выход).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from news_aggregator.alerts import AlertManager
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
    IImageHasher,
    ILemmaSetIndex,
    IPublisher,
    IRunStatsStore,
    ISimhashIndex,
    ISourceReader,
    ISourceRepository,
    IStorage,
)
from news_aggregator.core.models import PipelineStats, Source
from news_aggregator.core.pipeline import AggregatorPipeline
from news_aggregator.core.registry import ComponentNotRegisteredError, ComponentRegistry
from news_aggregator.healthcheck import heartbeat_path_for_db, write_heartbeat
from news_aggregator.redaction import redact_secrets

if TYPE_CHECKING:
    from telethon import TelegramClient

    from news_aggregator.bot.telegram_bot import AggregatorBot as AggregatorBotType

logger = logging.getLogger("news_aggregator")

# Как часто сторож зависаний проверяет прогресс прогона и сколько ждёт
# отменённый прогон, прежде чем завершить процесс.
_WATCHDOG_CHECK_SECONDS = 5.0
_STALLED_RUN_CANCEL_SECONDS = 5.0


class RunStalledError(RuntimeError):
    """Прогон дольше stall_timeout_seconds не продвигается ни на сообщение, ни на источник."""


class _RedactingFormatter(logging.Formatter):
    """Вырезает токены ботов из итоговой строки лога (сообщение, аргументы, трейсбек).

    Bot API кладёт токен прямо в URL (`/bot<token>/getUpdates`), и он легко
    попадает в логи HTTP-клиента или в текст исключения, а логи потом
    копируют в чаты и тикеты.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def _configure_logging(verbose: bool) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=[handler])
    # httpx на INFO пишет каждый запрос вместе с URL (а в нём токен бота), и
    # long polling бота даёт такую строку каждые ~10 секунд. Поэтому WARNING.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _build_storage(registry: ComponentRegistry, config: AppConfig) -> IStorage:
    return registry.storages.create(config.storage.name, **config.storage.params)  # type: ignore[return-value]


def _build_filters(registry: ComponentRegistry, config: AppConfig) -> list[IFilter]:
    return [
        registry.filters.create(c.name, **c.params)  # type: ignore[misc]
        for c in config.filters
    ]


def _build_deduplicators(
    registry: ComponentRegistry,
    config: AppConfig,
    storage: IStorage,
    shared_db_path: str,
    image_hasher: IImageHasher | None = None,
) -> list[IDeduplicator]:
    deduplicators: list[IDeduplicator] = []
    # char- и word-режимы SimHash дают несравнимые отпечатки для одного и
    # того же текста, поэтому каждому уникальному index_name — своя таблица
    # (см. docstring SqliteSimhashIndex). Кэш нужен, чтобы два
    # simhash_deduplicator с одинаковым index_name делили один и тот же
    # индекс/соединение, а не открывали второе к той же таблице. У
    # paraphrase_deduplicator (индекс лемм) — свой отдельный кэш по той же
    # логике, но своя таблица по умолчанию (seen_lemma_sets).
    simhash_index_cache: dict[str, ISimhashIndex] = {}
    lemma_index_cache: dict[str, ILemmaSetIndex] = {}
    for c in config.deduplicators:
        if c.name == "simhash_deduplicator":
            params = dict(c.params)
            index_name = str(params.pop("index_name", "seen_simhashes"))
            if index_name not in simhash_index_cache:
                simhash_index_cache[index_name] = registry.simhash_indexes.create(  # type: ignore[assignment]
                    "sqlite", db_path=shared_db_path, table_name=index_name
                )
            created = registry.deduplicators.create(
                c.name, index=simhash_index_cache[index_name], **params
            )
        elif c.name == "paraphrase_deduplicator":
            params = dict(c.params)
            index_name = str(params.pop("index_name", "seen_lemma_sets"))
            frequency_table = str(params.pop("frequency_table", "stem_frequencies"))
            if index_name not in lemma_index_cache:
                lemma_index_cache[index_name] = registry.lemma_indexes.create(  # type: ignore[assignment]
                    "sqlite", db_path=shared_db_path, table_name=index_name
                )
            if params.get("weighted_min_jaccard") is not None:
                params["frequency_index"] = registry.stem_frequency_indexes.create(
                    "sqlite", db_path=shared_db_path, table_name=frequency_table
                )
            created = registry.deduplicators.create(
                c.name, index=lemma_index_cache[index_name], **params
            )
        elif c.name == "image_deduplicator":
            params = dict(c.params)
            index_name = str(params.pop("index_name", "seen_image_hashes"))
            if image_hasher is None:
                logger.warning(
                    "image_deduplicator: нет Telegram-клиента, картинки сравниваться не будут"
                )
            created = registry.deduplicators.create(
                c.name,
                index=registry.image_hash_indexes.create(
                    "sqlite", db_path=shared_db_path, table_name=index_name
                ),
                hasher=image_hasher,
                **params,
            )
        else:
            created = registry.deduplicators.create(c.name, storage=storage, **c.params)
        deduplicators.append(cast(IDeduplicator, created))
    return deduplicators


async def _get_shared_telegram_client(
    secrets: TelegramSecrets, cache: dict[str, TelegramClient], *, connect: bool = True
) -> TelegramClient:
    """Строит и запускает единственный TelegramClient на процесс.

    Ридер и publisher оба используют один и тот же session-файл
    (TELEGRAM_SESSION_NAME), а Telethon не поддерживает параллельное
    подключение двух клиентов к одному session-файлу одновременно —
    вторая попытка падает с ``sqlite3.OperationalError: database is
    locked``. Поэтому клиент строится и стартует один раз и переиспользуется.

    connect=False только собирает клиент (это проверяет секреты), но не
    подключается к Telegram и не трогает session-файл — так работает
    ``--validate-config``, который можно безопасно запускать рядом с уже
    работающим контейнером.
    """
    if "client" not in cache:
        from news_aggregator.sources.telegram_client import build_telegram_client

        client = build_telegram_client(secrets)
        if connect:
            await client.start()
        cache["client"] = client
    return cache["client"]


async def _build_readers(
    registry: ComponentRegistry,
    telegram_client_cache: dict[str, TelegramClient],
    *,
    connect: bool = True,
) -> list[ISourceReader]:
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

    client = await _get_shared_telegram_client(secrets, telegram_client_cache, connect=connect)
    readers.append(registry.sources.create("telegram", client=client))  # type: ignore[arg-type]
    return readers


async def _build_publishers(
    registry: ComponentRegistry,
    config: AppConfig,
    telegram_client_cache: dict[str, TelegramClient],
    *,
    connect: bool = True,
) -> list[IPublisher]:
    publishers: list[IPublisher] = []
    for c in config.publishers:
        if c.name == "telegram_publisher":
            publishers.append(
                await _build_telegram_publisher(registry, c, telegram_client_cache, connect=connect)
            )
        else:
            publishers.append(registry.publishers.create(c.name, **c.params))  # type: ignore[arg-type]
    return publishers


async def _build_telegram_publisher(
    registry: ComponentRegistry,
    component_config: ComponentConfig,
    telegram_client_cache: dict[str, TelegramClient],
    *,
    connect: bool = True,
) -> IPublisher:
    try:
        from news_aggregator.sources.telegram_client import build_telegram_client  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "В конфигурации указан publisher 'telegram_publisher', но пакет telethon не установлен."
        ) from exc

    secrets = load_env_secrets()
    client = await _get_shared_telegram_client(secrets, telegram_client_cache, connect=connect)
    target_channel = component_config.params.get("target_channel") or secrets.target_channel
    if not target_channel:
        raise RuntimeError(
            "Не задан целевой канал для telegram_publisher: укажите "
            "TELEGRAM_TARGET_CHANNEL в .env или params.target_channel в config.yaml"
        )
    return registry.publishers.create(  # type: ignore[return-value]
        "telegram_publisher", client=client, target_channel=target_channel
    )


def _shared_sqlite_db_path(config: AppConfig) -> str:
    """Путь к общей SQLite-базе состояния.

    Используется и для репозитория источников, и для индекса SimHash-
    отпечатков — это просто ещё пара таблиц в той же БД, что и основное
    хранилище (courses/hashes/links), чтобы не плодить лишние секции
    конфигурации ради путей к файлам. Если storage не sqlite или путь не
    задан, используется тот же дефолт, что и у SqliteStorage.
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
    config: AppConfig, registry: ComponentRegistry, *, connect_telegram: bool = True
) -> tuple[AggregatorPipeline, ISourceRepository]:
    """Собирает AggregatorPipeline из конфигурации, используя реестр компонентов.

    connect_telegram=False собирает Telegram-компоненты без подключения к
    сети и без открытия session-файла (режим ``--validate-config``).

    Возвращает также ISourceRepository — тот же экземпляр, что использует
    пайплайн для получения списка источников на каждый прогон. main()
    передаёт его же в бота управления каналами, чтобы /add и /remove сразу
    были видны следующему run_once() без пересоздания пайплайна.
    """
    storage = _build_storage(registry, config)
    filters = _build_filters(registry, config)
    enrichers: list[IEnricher] = [
        registry.enrichers.create(c.name, **c.params)  # type: ignore[misc]
        for c in config.enrichers
    ]
    telegram_client_cache: dict[str, TelegramClient] = {}
    publishers = await _build_publishers(
        registry, config, telegram_client_cache, connect=connect_telegram
    )
    readers = await _build_readers(registry, telegram_client_cache, connect=connect_telegram)
    # Дедупликаторы — после ридеров: image_deduplicator скачивает картинки тем же
    # Telegram-клиентом, а он появляется только вместе с ридером или издателем.
    image_hasher: IImageHasher | None = None
    if "client" in telegram_client_cache and "telegram" in registry.image_hashers.available():
        image_hasher = registry.image_hashers.create(  # type: ignore[assignment]
            "telegram", client=telegram_client_cache["client"]
        )
    deduplicators = _build_deduplicators(
        registry, config, storage, _shared_sqlite_db_path(config), image_hasher
    )

    source_repository: ISourceRepository = registry.source_repositories.create(  # type: ignore[assignment]
        "sqlite", db_path=_shared_sqlite_db_path(config)
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
    alerts = config.alerts
    if alerts.enabled:
        print(
            f"Оповещения: включены (источник: {alerts.source_failure_threshold} подряд, "
            f"публикация: {alerts.publish_failure_threshold} подряд, "
            f"FloodWait > {alerts.flood_wait_alert_seconds:g} с, "
            f"cooldown {alerts.cooldown_seconds:g} с)"
        )
    else:
        print("Оповещения: выключены")
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
    secrets: TelegramSecrets,
    source_repository: ISourceRepository,
    stats_store: IRunStatsStore | None = None,
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
        stats_store=stats_store,
    )


async def _run_watched(pipeline: AggregatorPipeline, stall_timeout_seconds: int) -> PipelineStats:
    """run_once() под сторожем: если прогон не продвигается stall_timeout_seconds,
    он отменяется и бросается RunStalledError.

    Ограничивается не длительность прогона (после простоя он законно идёт долго),
    а время без прогресса: зависший сетевой вызов Telethon без таймаута иначе
    остановил бы цикл навсегда, а живой процесс Docker не перезапускает.
    """
    if stall_timeout_seconds <= 0:
        return await pipeline.run_once()

    task = asyncio.create_task(pipeline.run_once())
    check = min(_WATCHDOG_CHECK_SECONDS, stall_timeout_seconds / 4)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=check)
            if done:
                return task.result()
            idle = pipeline.seconds_since_progress()
            if idle >= stall_timeout_seconds:
                task.cancel()
                await asyncio.wait({task}, timeout=_STALLED_RUN_CANCEL_SECONDS)
                raise RunStalledError(f"прогон не продвигается {idle:.0f} с")
    except asyncio.CancelledError:
        task.cancel()
        raise


def _install_stop_handlers(on_signal: Callable[[str], None]) -> list[signal.Signals]:
    """Вешает on_signal на SIGTERM и SIGINT. Возвращает, что удалось повесить."""
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, on_signal, sig.name)
        except (NotImplementedError, RuntimeError):
            # Windows или не главный поток: остаётся поведение по умолчанию.
            continue
        installed.append(sig)
    return installed


async def _run_forever(
    pipeline: AggregatorPipeline,
    poll_interval_seconds: int,
    bot: AggregatorBotType | None,
    heartbeat_path: Path | None,
    stats_store: IRunStatsStore | None = None,
    alert_manager: AlertManager | None = None,
    *,
    stall_timeout_seconds: int = 0,
) -> None:
    """Цикл постоянного опроса источников, опционально вместе с ботом
    управления каналами (оба крутятся в одном event loop).

    После каждого run_once() итоги прогона пишутся в stats_store (для /stats;
    сбой записи только логируется и цикл не останавливает), затем
    обновляется heartbeat-файл (если heartbeat_path задан). По нему Docker
    HEALTHCHECK и внешний watchdog видят, что цикл жив и продвигается, а не
    завис. См. news_aggregator.healthcheck.

    Оповещения (alert_manager) обрабатываются ПОСЛЕ heartbeat: доставка — это
    сетевой вызов, и медленный Telegram не должен ложно сделать процесс
    «нездоровым». Сбой обработки оповещений цикл не останавливает.

    SIGTERM (так останавливает контейнер `docker stop`) и SIGINT не обрывают
    сообщение на середине: пайплайн дорабатывает текущее, сохраняет курсор, и
    цикл завершается. Иначе Python в роли PID 1 сигнал игнорирует, и через
    stop_grace_period Docker убивает процесс посреди публикации. Повторный
    сигнал останавливает немедленно.

    Если прогон завис (см. _run_watched), владельцу уходит оповещение, а
    RunStalledError выходит наружу: процесс завершается с ошибкой, и Docker
    его перезапускает.
    """
    stop_event = asyncio.Event()
    bot_task = asyncio.create_task(bot.run(stop_event)) if bot is not None else None
    shutdown = asyncio.Event()
    main_task = asyncio.current_task()

    def on_signal(name: str) -> None:
        if shutdown.is_set():
            logger.warning("Повторный %s: останавливаюсь немедленно", name)
            if main_task is not None:
                main_task.cancel()
            return
        logger.info("Получен %s: дорабатываю текущее сообщение и останавливаюсь", name)
        shutdown.set()
        pipeline.request_stop()

    installed = _install_stop_handlers(on_signal)

    try:
        while not shutdown.is_set():
            try:
                stats = await _run_watched(pipeline, stall_timeout_seconds)
            except RunStalledError as exc:
                logger.error("Прогон завис (%s), завершаю процесс для перезапуска", exc)
                if alert_manager is not None:
                    try:
                        await asyncio.wait_for(
                            alert_manager.notify(
                                "stalled",
                                f"⚠️ Прогон завис: {exc}. Перезапускаю процесс.",
                            ),
                            timeout=_STALLED_RUN_CANCEL_SECONDS * 2,
                        )
                    except Exception:
                        logger.exception("Не удалось отправить оповещение о зависании")
                raise
            if stats_store is not None:
                try:
                    await stats_store.record(stats)
                except Exception:
                    logger.exception("Не удалось сохранить статистику прогона")
            if heartbeat_path is not None:
                write_heartbeat(heartbeat_path)
            if alert_manager is not None:
                try:
                    await alert_manager.observe(stats)
                except Exception:
                    logger.exception("Сбой при обработке оповещений")
            if shutdown.is_set():
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown.wait(), timeout=poll_interval_seconds)
        logger.info("Остановлено по сигналу")
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Остановлено пользователем")
    finally:
        loop = asyncio.get_running_loop()
        for sig in installed:
            loop.remove_signal_handler(sig)
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
        pipeline, source_repository = await build_pipeline(
            config, registry, connect_telegram=not args.validate_config
        )
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
    stats_store = cast(
        IRunStatsStore,
        registry.run_stats_stores.create("sqlite", db_path=_shared_sqlite_db_path(config)),
    )
    bot = _try_build_bot(secrets, source_repository, stats_store)
    heartbeat_path = heartbeat_path_for_db(Path(_shared_sqlite_db_path(config)))
    alert_manager = AlertManager(config.alerts, sink=bot) if config.alerts.enabled else None
    if alert_manager is None:
        logger.info("Оповещения о сбоях выключены (alerts.enabled: false)")
    elif bot is None:
        logger.warning(
            "Оповещения о сбоях включены, но бот управления не настроен: "
            "они будут только в логе (задайте TELEGRAM_BOT_TOKEN и TELEGRAM_BOT_OWNER_ID)"
        )
    else:
        logger.info("Оповещения о сбоях включены, доставка владельцу через бота")
    try:
        await _run_forever(
            pipeline,
            config.pipeline.poll_interval_seconds,
            bot,
            heartbeat_path,
            stats_store,
            alert_manager,
            stall_timeout_seconds=config.pipeline.stall_timeout_seconds,
        )
    except RunStalledError:
        return 1
    return 0


def run() -> None:
    """Синхронная обёртка для entry_point в pyproject.toml."""
    exit_code = asyncio.run(_async_main())
    sys.exit(exit_code)


if __name__ == "__main__":
    run()
