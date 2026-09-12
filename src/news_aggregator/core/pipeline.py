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
и учитываются в PipelineStats.
"""

from __future__ import annotations

import logging

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

    async def _publish(self, message: ProcessedMessage) -> None:
        if self._dry_run:
            logger.info(
                "[dry-run] Сообщение %s/%s было бы опубликовано (не отправляю)",
                message.raw.source_id,
                message.raw.external_id,
            )
            return
        for publisher in self._publishers:
            try:
                await publisher.publish(message)
            except Exception:
                logger.exception(
                    "Издатель %s не смог опубликовать сообщение %s/%s",
                    type(publisher).__name__,
                    message.raw.source_id,
                    message.raw.external_id,
                )

    async def _process_source(self, source: Source, stats: PipelineStats) -> None:
        reader = self._reader_for(source)
        if reader is None:
            message = f"Нет ридера для источника '{source.id}' (kind={source.kind.value})"
            logger.error(message)
            stats.add_error(message)
            stats.sources_failed += 1
            return

        last_external_id = await self._storage.get_last_external_id(source.id)
        newest_external_id = last_external_id
        processed_count = 0

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
                stats.messages_fetched += 1
                newest_external_id = raw.external_id

                try:
                    await self._handle_message(raw, stats)
                except Exception:
                    logger.exception(
                        "Ошибка обработки сообщения %s/%s, продолжаю со следующим",
                        source.id,
                        raw.external_id,
                    )
                    stats.add_error(f"Ошибка обработки сообщения {source.id}/{raw.external_id}")
        except Exception as exc:
            message = f"Источник '{source.id}' упал: {exc}"
            logger.exception(message)
            stats.add_error(message)
            stats.sources_failed += 1

        if newest_external_id is not None and newest_external_id != last_external_id:
            await self._storage.set_last_external_id(source.id, newest_external_id)

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

        await self._publish(message)
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

        for source in active_sources:
            await self._process_source(source, stats)

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
