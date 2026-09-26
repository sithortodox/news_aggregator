"""Интерфейсы расширяемых компонентов.

Любая конкретная реализация (Telegram, SQLite, консоль и т.д.) должна
реализовывать один из этих интерфейсов и ничего не знать о пайплайне
напрямую. Пайплайн, в свою очередь, работает только с этими интерфейсами
и не зависит от конкретных реализаций.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from news_aggregator.core.models import (
    DeduplicationResult,
    ProcessedMessage,
    RawMessage,
    Source,
)


class ISourceReader(ABC):
    """Источник сырых сообщений (Telegram-канал, группа, фейковый источник)."""

    @abstractmethod
    def supports(self, source: Source) -> bool:
        """Возвращает True, если этот reader умеет читать данный источник."""

    @abstractmethod
    async def read_new_messages(
        self, source: Source, since_external_id: str | None
    ) -> AsyncIterator[RawMessage]:
        """Асинхронно возвращает новые сообщения источника.

        Args:
            source: Источник, из которого нужно читать.
            since_external_id: Идентификатор последнего уже обработанного
                сообщения (для инкрементального чтения). None означает,
                что источник читается впервые.
        """
        # pragma: no cover - реализуется наследниками
        if False:  # noqa: SIM108 -- нужен yield, чтобы метод был генератором
            yield


class IFilter(ABC):
    """Фильтр, решающий, должно ли сообщение быть отброшено."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Короткое имя фильтра для логов и статистики."""

    @abstractmethod
    def should_drop(self, message: ProcessedMessage) -> tuple[bool, str | None]:
        """Проверяет сообщение.

        Returns:
            Кортеж (should_drop, reason). Если should_drop is True,
            reason должен содержать человекочитаемое объяснение.
        """


class IDeduplicator(ABC):
    """Компонент, определяющий, является ли сообщение дублем."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Короткое имя дедупликатора для логов и статистики."""

    @abstractmethod
    async def check(self, message: ProcessedMessage) -> DeduplicationResult:
        """Проверяет сообщение на дублирование по своей стратегии."""

    @abstractmethod
    async def remember(self, message: ProcessedMessage) -> None:
        """Запоминает сообщение как опубликованное/уникальное."""


class IEnricher(ABC):
    """Компонент, обогащающий сообщение дополнительными данными.

    Например: разворачивание коротких ссылок, определение языка,
    категоризация и т.п. Обогащение не должно менять text_hash,
    вычисленный до обогащения.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Короткое имя обогатителя для логов и статистики."""

    @abstractmethod
    async def enrich(self, message: ProcessedMessage) -> ProcessedMessage:
        """Возвращает новое сообщение с добавленными данными."""


class IPublisher(ABC):
    """Издатель, публикующий уникальные сообщения в целевое место."""

    @abstractmethod
    async def publish(self, message: ProcessedMessage) -> None:
        """Публикует сообщение. Не должен кидать исключения наружу пайплайна
        без необходимости — ошибки логируются и учитываются в статистике
        на уровне пайплайна."""


class IStorage(ABC):
    """Хранилище состояния агрегатора (курсоры источников, история хэшей)."""

    @abstractmethod
    async def get_last_external_id(self, source_id: str) -> str | None:
        """Возвращает external_id последнего обработанного сообщения источника."""

    @abstractmethod
    async def set_last_external_id(self, source_id: str, external_id: str) -> None:
        """Сохраняет external_id последнего обработанного сообщения источника."""

    @abstractmethod
    async def has_hash(self, text_hash: str) -> bool:
        """Проверяет, встречался ли ранее такой хэш текста."""

    @abstractmethod
    async def save_hash(self, text_hash: str, source_id: str, external_id: str) -> None:
        """Сохраняет хэш текста опубликованного сообщения."""

    @abstractmethod
    async def has_link(self, link: str) -> bool:
        """Проверяет, встречалась ли ранее такая ссылка."""

    @abstractmethod
    async def save_link(self, link: str, source_id: str, external_id: str) -> None:
        """Сохраняет ссылку опубликованного сообщения."""

    @abstractmethod
    async def close(self) -> None:
        """Освобождает ресурсы хранилища (соединения и т.п.)."""


class ISimhashIndex(ABC):
    """Хранилище SimHash-отпечатков недавних сообщений для поиска почти-дублей.

    Отдельный интерфейс от IStorage (по той же логике, что и
    ISourceRepository): это не точное совпадение "видели/не видели", а
    поиск по расстоянию Хэмминга среди отпечатков за ограниченное окно
    времени — принципиально другая форма запроса.

    Реализация обязана сама ограничивать объём хранимых данных (см.
    purge_older_than) — вызывающий код (SimHashDeduplicator) вызывает его
    при каждом remember(), но реализация вправе делать это и по-другому
    (например, по расписанию), лишь бы find_recent не деградировал
    бесконечно растущим сканированием.
    """

    @abstractmethod
    async def find_recent(self, since: datetime) -> list[int]:
        """Возвращает SimHash-отпечатки всех сообщений, сохранённых начиная с since."""

    @abstractmethod
    async def save(self, simhash: int, source_id: str, external_id: str) -> None:
        """Сохраняет отпечаток сообщения с текущей меткой времени."""

    @abstractmethod
    async def purge_older_than(self, cutoff: datetime) -> int:
        """Удаляет отпечатки старше cutoff. Возвращает число удалённых записей."""

    @abstractmethod
    async def close(self) -> None:
        """Освобождает ресурсы хранилища (соединения и т.п.)."""


class ILemmaSetIndex(ABC):
    """Хранилище множеств лемм недавних сообщений для поиска пересказов.

    В отличие от ISimhashIndex (сравнение по расстоянию Хэмминга между
    числами-отпечатками), здесь единица сравнения — множество строк (лемм),
    а метрика похожести — коэффициент Жаккара (см.
    news_aggregator.core.lemmatize.jaccard_similarity). Нужен отдельный
    интерфейс, поскольку форма данных и запроса другая: не число, а
    множество строк на каждое сообщение.

    Как и у ISimhashIndex, реализация обязана сама ограничивать объём
    хранимых данных (purge_older_than вызывается при каждом remember()
    дедупликатором).
    """

    @abstractmethod
    async def find_recent(self, since: datetime) -> list[frozenset[str]]:
        """Возвращает множества лемм всех сообщений, сохранённых с since."""

    @abstractmethod
    async def save(self, lemmas: frozenset[str], source_id: str, external_id: str) -> None:
        """Сохраняет множество лемм сообщения с текущей меткой времени."""

    @abstractmethod
    async def purge_older_than(self, cutoff: datetime) -> int:
        """Удаляет записи старше cutoff. Возвращает число удалённых записей."""

    @abstractmethod
    async def close(self) -> None:
        """Освобождает ресурсы хранилища (соединения и т.п.)."""


class ISourceRepository(ABC):
    """Хранилище списка источников (каналов/групп), управляемого динамически.

    В отличие от IStorage (курсоры и история хэшей — внутреннее состояние
    пайплайна), это хранилище отвечает за сам список источников, который
    может изменяться во время работы приложения — например, через команды
    Telegram-бота (/add, /remove, /list) — без перезапуска процесса.
    AggregatorPipeline читает список активных источников из этого
    хранилища в начале каждого прогона.
    """

    @abstractmethod
    async def list_sources(self) -> list[Source]:
        """Возвращает все источники (включая выключенные), в порядке добавления."""

    @abstractmethod
    async def get_source(self, source_id: str) -> Source | None:
        """Возвращает источник по id или None, если не найден."""

    @abstractmethod
    async def add_source(self, source: Source) -> bool:
        """Добавляет источник.

        Returns:
            True, если источник добавлен. False, если источник с таким же
            id ИЛИ identifier уже существует (дубликат) — источник в этом
            случае не добавляется повторно.
        """

    @abstractmethod
    async def remove_source(self, source_id: str) -> bool:
        """Удаляет источник по id. Возвращает True, если что-то удалено."""

    @abstractmethod
    async def set_enabled(self, source_id: str, enabled: bool) -> bool:
        """Включает/выключает источник по id. Возвращает True, если найден."""

    @abstractmethod
    async def close(self) -> None:
        """Освобождает ресурсы хранилища (соединения и т.п.)."""
