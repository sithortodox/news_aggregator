"""Доменные модели ядра.

Модели не зависят от Telegram, SQLite или любых внешних сервисов.
Они описывают только предметную область: источники, сырые и обработанные
сообщения, результаты дедупликации и статистику работы пайплайна.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class SourceKind(StrEnum):
    """Тип источника сообщений."""

    TELEGRAM_CHANNEL = "telegram_channel"
    TELEGRAM_GROUP = "telegram_group"
    FAKE = "fake"


@dataclass(frozen=True, slots=True)
class Source:
    """Описание источника, из которого читаются сообщения.

    Attributes:
        id: Уникальный идентификатор источника внутри конфигурации.
        kind: Тип источника (канал, группа, фейковый источник и т.д.).
        identifier: Технический идентификатор источника для конкретного
            адаптера (например, username канала в Telegram).
        display_name: Человекочитаемое имя, используется в логах и публикациях.
        enabled: Признак того, что источник активен и должен опрашиваться.
    """

    id: str
    kind: SourceKind
    identifier: str
    display_name: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class MediaAttachment:
    """Непрозрачная ссылка на медиавложение сообщения-источника.

    Ядро намеренно не заглядывает внутрь ``native_ref`` — это объект,
    специфичный для конкретного адаптера-источника (например, объект
    media из Telethon), и интерпретировать его умеет только парный
    адаптер-publisher того же источника. Так ядро остаётся независимым
    от Telegram/Telethon, но при этом может пронести вложение от ридера
    до publisher'а без скачивания и повторной загрузки файла.

    Attributes:
        kind: Человекочитаемый тип вложения ("photo", "document" и т.д.),
            используется только для логов.
        native_ref: Специфичный для адаптера объект медиа.
    """

    kind: str
    native_ref: object


@dataclass(frozen=True, slots=True)
class RawMessage:
    """Сырое сообщение, полученное из источника без какой-либо обработки.

    Attributes:
        source_id: Идентификатор источника (см. Source.id).
        external_id: Идентификатор сообщения в системе источника
            (например, message_id в Telegram). Используется для отслеживания
            уже прочитанных сообщений.
        text: Исходный текст сообщения как есть.
        posted_at: Время публикации сообщения в источнике.
        fetched_at: Время получения сообщения агрегатором.
        media: Вложенный медиафайл (фото/документ), если есть, иначе None.
    """

    source_id: str
    external_id: str
    text: str
    posted_at: datetime
    fetched_at: datetime
    media: MediaAttachment | None = None


@dataclass(frozen=True, slots=True)
class ProcessedMessage:
    """Сообщение после нормализации и извлечения метаданных.

    Attributes:
        raw: Исходное сырое сообщение, из которого получено это сообщение.
        normalized_text: Текст после нормализации (нижний регистр, схлопнутые
            пробелы, удалённая пунктуация и т.д. — конкретные правила заданы
            в normalize_text).
        text_hash: sha256-хэш нормализованного текста, используется для
            дедупликации по содержимому.
        links: Список ссылок, извлечённых из текста сообщения.
        is_filtered_out: Признак того, что сообщение отсеяно каким-либо
            фильтром и не должно публиковаться.
        filter_reason: Причина отсева, если is_filtered_out is True.
    """

    raw: RawMessage
    normalized_text: str
    text_hash: str
    links: tuple[str, ...] = field(default_factory=tuple)
    is_filtered_out: bool = False
    filter_reason: str | None = None


@dataclass(frozen=True, slots=True)
class DeduplicationResult:
    """Результат проверки сообщения на дублирование.

    Attributes:
        is_duplicate: True, если сообщение признано дублем ранее увиденного.
        reason: Человекочитаемое объяснение решения (для логов и отладки).
        matched_hash: Хэш совпавшего сообщения, если найден дубль по тексту.
        matched_link: Ссылка, вызвавшая совпадение, если найден дубль по ссылке.
    """

    is_duplicate: bool
    reason: str
    matched_hash: str | None = None
    matched_link: str | None = None


@dataclass(slots=True)
class PipelineStats:
    """Статистика одного прогона пайплайна.

    Поля инкрементируются пайплайном по ходу обработки и в конце прогона
    логируются и/или возвращаются вызывающему коду.
    """

    sources_total: int = 0
    sources_failed: int = 0
    messages_fetched: int = 0
    messages_filtered_out: int = 0
    messages_duplicate: int = 0
    messages_published: int = 0
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        """Регистрирует ошибку, не прерывая выполнение пайплайна."""
        self.errors.append(message)
