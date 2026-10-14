"""AggregatorPipeline — основной процесс агрегации новостей.

Пайплайн работает исключительно через интерфейсы (ISourceReader,
ISourceRepository, IFilter, IDeduplicator, IEnricher, IPublisher, IStorage)
и ничего не знает о конкретных реализациях (Telegram, SQLite, консоль). Он
не меняется при добавлении новых фильтров/дедупликаторов/обогатителей/
издателей — их набор передаётся в конструктор. Список источников тоже не
фиксирован в конструкторе — он читается из ISourceRepository на каждый
прогон, чтобы каналы можно было добавлять/удалять на лету (например,
командами Telegram-бота) без перезапуска процесса.

Падение одного источника или одного сообщения не должно останавливать
обработку остальных источников — все ошибки перехватываются на границах
и учитываются в PipelineStats (в том числе по каждому источнику отдельно и
сбои издателей — по ним строятся алерты).

Ограничение частоты запросов (RateLimitedError, в Telegram — FloodWait) —
не сбой источника: пайплайн приостанавливает ВСЕ обращения на требуемое
время (повторные запросы во время FloodWait его только удлиняют), не
продвигает курсор за сообщение, на котором остановился, и не помечает его
увиденным — после паузы оно будет обработано заново, а не потеряно.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from news_aggregator.core.errors import RateLimitedError
from news_aggregator.core.interfaces import (
    IDeduplicator,
    IEnricher,
    IFilter,
    IPublisher,
    ISourceReader,
    ISourceRepository,
    IStorage,
)
from news_aggregator.core.models import (
    PipelineStats,
    ProcessedMessage,
    RawMessage,
    Source,
)
from news_aggregator.core.text_utils import compute_text_hash, extract_links, normalize_text

logger = logging.getLogger(__name__)

# Запас поверх требуемой паузы: ровно в момент окончания FloodWait сервер
# может ещё отвечать отказом.
_RATE_LIMIT_MARGIN_SECONDS = 1.0
_MAX_ERROR_TEXT = 300


def _short_error(text: str) -> str:
    return text if len(text) <= _MAX_ERROR_TEXT else text[: _MAX_ERROR_TEXT - 1] + "…"


class AggregatorPipeline:
    """Оркестрирует полный цикл: чтение -> обработка -> публикация."""

    def __init__(
        self,
        source_repository: ISourceRepository,
        readers: list[ISourceReader],
        filters: list[IFilter],
        deduplicators: list[IDeduplicator],
        enrichers: list[IEnricher],
        publishers: list[IPublisher],
        storage: IStorage,
        max_messages_per_source: int = 100,
        dry_run: bool = True,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source_repository = source_repository
        self._readers = readers
        self._filters = filters
        self._deduplicators = deduplicators
        self._enrichers = enrichers
        self._publishers = publishers
        self._storage = storage
        self._max_messages_per_source = max_messages_per_source
        self._dry_run = dry_run
        self._clock = clock
        # Момент (по clock), до которого все обращения к внешним сервисам приостановлены.
        self._paused_until = 0.0

    def _reader_for(self, source: Source) -> ISourceReader | None:
        for reader in self._readers:
            if reader.supports(source):
                return reader
        return None

    def _process_raw_message(self, raw: RawMessage) -> ProcessedMessage:
        normalized = normalize_text(raw.text)
        return ProcessedMessage(
            raw=raw,
            normalized_text=normalized,
            text_hash=compute_text_hash(normalized),
            links=extract_links(raw.text),
        )

    def _apply_filters(self, message: ProcessedMessage) -> ProcessedMessage:
        for flt in self._filters:
            should_drop, reason = flt.should_drop(message)
            if should_drop:
                logger.info(
                    "Сообщение %s/%s отфильтровано (%s): %s",
                    message.raw.source_id,
                    message.raw.external_id,
                    flt.name,
                    reason,
                )
                return ProcessedMessage(
                    raw=message.raw,
                    normalized_text=message.normalized_text,
                    text_hash=message.text_hash,
                    links=message.links,
                    is_filtered_out=True,
                    filter_reason=f"{flt.name}: {reason}",
                )
        return message

    async def _apply_enrichers(self, message: ProcessedMessage) -> ProcessedMessage:
        for enricher in self._enrichers:
            try:
                message = await enricher.enrich(message)
            except Exception:
                logger.exception(
                    "Обогатитель %s упал на сообщении %s/%s, пропускаю обогащение",
                    enricher.name,
                    message.raw.source_id,
                    message.raw.external_id,
                )
        return message

    async def _is_duplicate(self, message: ProcessedMessage) -> bool:
        for dedup in self._deduplicators:
            result = await dedup.check(message)
            if result.is_duplicate:
                logger.info(
                    "Сообщение %s/%s признано дублем (%s): %s",
                    message.raw.source_id,
                    message.raw.external_id,
                    dedup.name,
                    result.reason,
                )
                return True
        return False

    async def _remember(self, message: ProcessedMessage) -> None:
        for dedup in self._deduplicators:
            await dedup.remember(message)

    def _register_rate_limit(self, exc: RateLimitedError, where: str, stats: PipelineStats) -> None:
        seconds = exc.retry_after_seconds
        self._paused_until = max(
            self._paused_until, self._clock() + seconds + _RATE_LIMIT_MARGIN_SECONDS
        )
        stats.flood_wait_seconds = max(stats.flood_wait_seconds, seconds)
        logger.warning(
            "Ограничение частоты запросов (%s): пауза %.0f с, обращения приостановлены",
            where,
            seconds,
        )

    async def _publish(self, message: ProcessedMessage, stats: PipelineStats) -> bool:
        """Публикует сообщение. True — оно доставлено хотя бы одним издателем.

        Если ни один издатель не справился (кроме ограничения частоты — тогда
        бросается RateLimitedError), сообщение НЕ считается опубликованным:
        вызывающий код не запоминает его как увиденное.
        """
        if self._dry_run:
            logger.info(
                "[dry-run] Сообщение %s/%s было бы опубликовано (не отправляю)",
                message.raw.source_id,
                message.raw.external_id,
            )
            return True

        succeeded = 0
        limited: RateLimitedError | None = None
        for publisher in self._publishers:
            try:
                await publisher.publish(message)
                succeeded += 1
            except RateLimitedError as exc:
                limited = exc
            except Exception as exc:
                logger.exception(
                    "Издатель %s не смог опубликовать сообщение %s/%s",
                    type(publisher).__name__,
                    message.raw.source_id,
                    message.raw.external_id,
                )
                stats.publish_failures += 1
                stats.publish_error = _short_error(
                    f"{type(publisher).__name__}: {type(exc).__name__}: {exc}"
                )

        if limited is not None:
            if succeeded == 0:
                # Никто не опубликовал: пусть пайплайн повторит сообщение после паузы.
                raise limited
            # Часть издателей уже доставила — повтор дал бы им дубль. Засчитываем
            # ограничение как сбой одного издателя и всё равно ставим паузу.
            stats.publish_failures += 1
            stats.publish_error = _short_error(f"ограничение частоты запросов: {limited}")
            self._register_rate_limit(limited, "издатель", stats)

        if succeeded == 0:
            logger.error(
                "Сообщение %s/%s не опубликовано ни одним издателем и пропущено",
                message.raw.source_id,
                message.raw.external_id,
            )
        return succeeded > 0

    async def _process_source(self, source: Source, stats: PipelineStats) -> bool:
        """Обрабатывает один источник. True — упёрлись в ограничение частоты
        запросов и остальные источники в этом прогоне трогать не нужно."""
        reader = self._reader_for(source)
        if reader is None:
            message = f"Нет ридера для источника '{source.id}' (kind={source.kind.value})"
            logger.error(message)
            stats.add_error(message)
            stats.sources_failed += 1
            stats.failed_sources.append(source.id)
            stats.source_errors[source.id] = message
            return False

        last_external_id = await self._storage.get_last_external_id(source.id)
        newest_external_id = last_external_id
        processed_count = 0
        rate_limited = False

        try:
            async for raw in reader.read_new_messages(source, last_external_id):
                if processed_count >= self._max_messages_per_source:
                    logger.warning(
                        "Достигнут лимит max_messages_per_source=%d для источника '%s'",
                        self._max_messages_per_source,
                        source.id,
                    )
                    break
                processed_count += 1

                try:
                    await self._handle_message(raw, stats)
                except RateLimitedError:
                    # Курсор остаётся перед этим сообщением — после паузы его
                    # обработают заново (оно не запомнено как увиденное).
                    raise
                except Exception:
                    logger.exception(
                        "Ошибка обработки сообщения %s/%s, продолжаю со следующим",
                        source.id,
                        raw.external_id,
                    )
                    stats.add_error(f"Ошибка обработки сообщения {source.id}/{raw.external_id}")

                stats.messages_fetched += 1
                newest_external_id = raw.external_id
        except RateLimitedError as exc:
            rate_limited = True
            self._register_rate_limit(exc, f"источник '{source.id}'", stats)
        except Exception as exc:
            message = f"Источник '{source.id}' упал: {exc}"
            logger.exception(message)
            stats.add_error(message)
            stats.sources_failed += 1
            stats.failed_sources.append(source.id)
            stats.source_errors[source.id] = _short_error(f"{type(exc).__name__}: {exc}")
        else:
            stats.succeeded_sources.append(source.id)

        if newest_external_id is not None and newest_external_id != last_external_id:
            await self._storage.set_last_external_id(source.id, newest_external_id)
        return rate_limited

    async def _handle_message(self, raw: RawMessage, stats: PipelineStats) -> None:
        message = self._process_raw_message(raw)
        message = self._apply_filters(message)

        if message.is_filtered_out:
            stats.messages_filtered_out += 1
            return

        message = await self._apply_enrichers(message)

        if await self._is_duplicate(message):
            stats.messages_duplicate += 1
            return

        if await self._publish(message, stats):
            await self._remember(message)
            stats.messages_published += 1

    async def run_once(self) -> PipelineStats:
        """Выполняет один проход по всем активным источникам.

        Список источников запрашивается из ISourceRepository заново на
        каждый прогон — это позволяет добавлять/удалять/приостанавливать
        каналы во время работы (например, командами Telegram-бота) без
        перезапуска процесса.
        """
        stats = PipelineStats()
        all_sources = await self._source_repository.list_sources()
        active_sources = [s for s in all_sources if s.enabled]
        stats.sources_total = len(active_sources)

        remaining = self._paused_until - self._clock()
        if remaining > 0:
            logger.info(
                "Прогон пропущен: пауза из-за ограничения частоты запросов, осталось %.0f с",
                remaining,
            )
            return stats

        for source in active_sources:
            if await self._process_source(source, stats):
                break

        logger.info(
            "Прогон завершён: источники=%d (упало=%d), получено=%d, "
            "отфильтровано=%d, дублей=%d, опубликовано=%d, ошибок=%d",
            stats.sources_total,
            stats.sources_failed,
            stats.messages_fetched,
            stats.messages_filtered_out,
            stats.messages_duplicate,
            stats.messages_published,
            len(stats.errors),
        )
        return stats
