"""Загрузка конфигурации из YAML-файла и переменных окружения.

Секреты (TELEGRAM_API_ID, TELEGRAM_API_HASH и т.д.) читаются отдельно,
функцией load_env_secrets, и никогда не сохраняются в объекты AppConfig —
это единственное место, где .env вообще упоминается.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

import yaml
from dotenv import load_dotenv

from news_aggregator.config.schema import (
    AppConfig,
    ComponentConfig,
    PipelineConfig,
    StorageConfig,
    component_from_dict,
    source_from_any,
)
from news_aggregator.core.models import Source


class ConfigError(Exception):
    """Ошибка чтения или валидации конфигурации."""


def _require_list(raw: dict[str, object], key: str) -> list[dict[str, object]]:
    value = raw.get(key, [])
    if not isinstance(value, list):
        raise ConfigError(f"Секция '{key}' должна быть списком")
    return value


def _resolve_sources(raw_items: list[object]) -> tuple[Source, ...]:
    """Строит Source из каждого элемента 'sources', разрешая конфликты id.

    Поддерживает и короткие строки-каналы, и (частичные) словари — см.
    source_from_any. Если у двух источников совпал автоматически выведенный
    id, к id второго добавляется числовой суффикс (_2, _3, ...). Если же
    два элемента списка — это буквально один и тот же identifier, это
    считается ошибкой конфигурации (скорее всего, канал добавлен дважды
    по невнимательности).
    """
    seen_ids: dict[str, str] = {}  # id -> identifier, для какого он был занят
    seen_identifiers: set[str] = set()
    sources: list[Source] = []

    for raw_item in raw_items:
        source = source_from_any(raw_item)

        if source.identifier in seen_identifiers:
            raise ConfigError(f"Канал '{source.identifier}' указан в 'sources' более одного раза")
        seen_identifiers.add(source.identifier)

        source_id = source.id
        suffix = 2
        while source_id in seen_ids:
            source_id = f"{source.id}_{suffix}"
            suffix += 1
        if source_id != source.id:
            source = replace(source, id=source_id)
        seen_ids[source_id] = source.identifier

        sources.append(source)

    return tuple(sources)


def load_app_config(path: str | Path) -> AppConfig:
    """Читает и валидирует конфигурацию приложения из YAML-файла.

    Не читает и не требует секретов — для секретов см. load_env_secrets.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Файл конфигурации не найден: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Некорректный YAML в {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Корень конфигурации должен быть отображением (mapping)")

    sources_raw = raw.get("sources", [])
    if not isinstance(sources_raw, list):
        raise ConfigError("Секция 'sources' должна быть списком")

    try:
        sources = _resolve_sources(sources_raw)
        filters = tuple(component_from_dict(f) for f in _require_list(raw, "filters"))
        deduplicators = tuple(component_from_dict(d) for d in _require_list(raw, "deduplicators"))
        enrichers = tuple(component_from_dict(e) for e in _require_list(raw, "enrichers"))
        publishers = tuple(component_from_dict(p) for p in _require_list(raw, "publishers"))

        storage_raw = raw.get("storage") or {}
        storage = StorageConfig(
            name=str(storage_raw.get("name", "sqlite")),
            params=dict(storage_raw.get("params") or {}),
        )

        pipeline_raw = raw.get("pipeline") or {}
        pipeline = PipelineConfig(
            dry_run=bool(pipeline_raw.get("dry_run", True)),
            poll_interval_seconds=int(pipeline_raw.get("poll_interval_seconds", 60)),
            max_messages_per_source=int(pipeline_raw.get("max_messages_per_source", 100)),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise ConfigError(f"Ошибка в структуре конфигурации: {exc}") from exc

    if not publishers:
        raise ConfigError("Должен быть указан хотя бы один издатель (publishers)")

    return AppConfig(
        sources=sources,
        filters=filters,
        deduplicators=deduplicators,
        enrichers=enrichers,
        publishers=publishers,
        storage=storage,
        pipeline=pipeline,
    )


@dataclass(frozen=True, slots=True)
class TelegramSecrets:
    """Секреты Telegram, полученные исключительно из переменных окружения."""

    api_id: int | None
    api_hash: str | None
    session_name: str
    target_channel: str | None
    bot_token: str | None
    bot_owner_id: int | None


def load_env_secrets(dotenv_path: str | Path | None = None) -> TelegramSecrets:
    """Загружает секреты из .env / окружения. Никогда не читает и не пишет
    секреты в YAML-конфигурацию и не логирует их значения."""
    load_dotenv(dotenv_path=dotenv_path, override=False)

    api_id_raw = os.getenv("TELEGRAM_API_ID")
    bot_owner_id_raw = os.getenv("TELEGRAM_BOT_OWNER_ID")
    return TelegramSecrets(
        api_id=int(api_id_raw) if api_id_raw else None,
        api_hash=os.getenv("TELEGRAM_API_HASH") or None,
        session_name=os.getenv("TELEGRAM_SESSION_NAME", "news_aggregator"),
        target_channel=os.getenv("TELEGRAM_TARGET_CHANNEL") or None,
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        bot_owner_id=int(bot_owner_id_raw) if bot_owner_id_raw else None,
    )


def component_config_to_kwargs(config: ComponentConfig) -> dict[str, object]:
    """Вспомогательная функция: params компонента как kwargs для реестра."""
    return dict(config.params)
