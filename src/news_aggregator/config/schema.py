"""Типизированная схема конфигурации приложения.

Секреты (API-ключи, токены) сюда НЕ попадают — они берутся из переменных
окружения (.env) отдельно, в момент создания конкретных адаптеров
(Telegram и т.п.), а не хранятся в YAML или в этих датаклассах.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from news_aggregator.core.models import Source, SourceKind

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True, slots=True)
class ComponentConfig:
    """Конфигурация одного компонента: имя фабрики в реестре + параметры.

    Пример в YAML:
        - name: length_filter
          params:
            min_length: 20
    """

    name: str
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """Конфигурация хранилища состояния."""

    name: str = "sqlite"
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Общие настройки поведения пайплайна."""

    dry_run: bool = True
    poll_interval_seconds: int = 60
    max_messages_per_source: int = 100


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Корневая конфигурация приложения, полученная из config.yaml."""

    sources: tuple[Source, ...]
    filters: tuple[ComponentConfig, ...]
    deduplicators: tuple[ComponentConfig, ...]
    enrichers: tuple[ComponentConfig, ...]
    publishers: tuple[ComponentConfig, ...]
    storage: StorageConfig
    pipeline: PipelineConfig


def slugify_identifier(identifier: str) -> str:
    """Превращает идентификатор канала в безопасный short id источника.

    Примеры: "@meduzalive" -> "meduzalive", "https://t.me/rian_ru" -> "t_me_rian_ru"
    """
    stripped = identifier.strip().lstrip("@")
    slug = _SLUG_RE.sub("_", stripped.lower()).strip("_")
    return slug or "source"


def source_from_any(
    raw: object, *, default_kind: SourceKind = SourceKind.TELEGRAM_CHANNEL
) -> Source:
    """Строит Source из элемента списка 'sources' в YAML.

    Поддерживает два формата, чтобы добавление канала было максимально
    простым:

    1. Короткая строка — просто username/ссылка канала:
           sources:
             - "@some_news_channel"
       Автоматически становится Source с kind=telegram_channel,
       identifier="@some_news_channel", id и display_name, выведенными
       из идентификатора.

    2. Полный или частичный словарь — любые поля можно опустить, кроме
       identifier:
           sources:
             - identifier: "@some_group"
               kind: telegram_group      # необязательно, по умолчанию telegram_channel
               id: my_group              # необязательно, иначе выводится из identifier
               display_name: "Моя группа"  # необязательно
               enabled: false             # необязательно, по умолчанию true
    """
    if isinstance(raw, str):
        identifier = raw
        kind = default_kind
        source_id = slugify_identifier(identifier)
        display_name = identifier
        enabled = True
    elif isinstance(raw, dict):
        if "identifier" not in raw:
            raise ValueError(
                f"У источника отсутствует обязательное поле 'identifier': {raw!r}"
            )
        identifier = str(raw["identifier"])
        kind_raw = raw.get("kind")
        try:
            kind = SourceKind(str(kind_raw)) if kind_raw is not None else default_kind
        except ValueError as exc:
            valid = ", ".join(k.value for k in SourceKind)
            raise ValueError(
                f"Неизвестный тип источника '{kind_raw}'. Допустимые: {valid}"
            ) from exc
        source_id = str(raw.get("id") or slugify_identifier(identifier))
        display_name = str(raw.get("display_name", identifier))
        enabled = bool(raw.get("enabled", True))
    else:
        raise ValueError(
            f"Источник должен быть строкой (username канала) или словарём, получено: {raw!r}"
        )

    return Source(
        id=source_id,
        kind=kind,
        identifier=identifier,
        display_name=display_name,
        enabled=enabled,
    )


def component_from_dict(raw: dict[str, object]) -> ComponentConfig:
    """Строит ComponentConfig из словаря, полученного при парсинге YAML."""
    params_raw = raw.get("params") or {}
    if not isinstance(params_raw, dict):
        raise ValueError(f"'params' должен быть словарём, получено: {type(params_raw)!r}")
    return ComponentConfig(name=str(raw["name"]), params=dict(params_raw))
