"""Бизнес-логика команд Telegram-бота управления списком каналов.

Каждая функция принимает уже распарсенные аргументы команды и
ISourceRepository, а возвращает готовый текст ответа — без единого
упоминания python-telegram-bot. Поэтому весь модуль полностью
тестируется без установленной библиотеки бота; bot/telegram_bot.py —
тонкая обвязка, которая только достаёт аргументы из Update/Context и
вызывает эти функции.
"""

from __future__ import annotations

from news_aggregator.config.schema import slugify_identifier
from news_aggregator.core.interfaces import ISourceRepository
from news_aggregator.core.models import Source, SourceKind

USAGE = (
    "Команды:\n"
    "/list — показать все каналы\n"
    "/add <identifier> [kind] — добавить канал "
    "(kind: telegram_channel по умолчанию, или telegram_group)\n"
    "/remove <id> — удалить канал\n"
    "/pause <id> — временно выключить канал (без удаления)\n"
    "/resume <id> — снова включить канал"
)


def _format_source_line(source: Source) -> str:
    status = "🟢" if source.enabled else "⏸"
    return f"{status} {source.id} — {source.kind.value} ({source.identifier})"


async def cmd_list(repository: ISourceRepository) -> str:
    """Возвращает список всех источников (включая выключенные)."""
    sources = await repository.list_sources()
    if not sources:
        return "Список каналов пуст. Добавьте: /add @channel_username"
    lines = [_format_source_line(s) for s in sources]
    return "Каналы:\n" + "\n".join(lines)


async def cmd_add(args: list[str], repository: ISourceRepository) -> str:
    """Добавляет канал: /add <identifier> [kind]."""
    if not args:
        return "Использование: /add <identifier> [kind]\n\n" + USAGE

    identifier = args[0]
    kind_raw = args[1] if len(args) > 1 else SourceKind.TELEGRAM_CHANNEL.value
    try:
        kind = SourceKind(kind_raw)
    except ValueError:
        valid = ", ".join(k.value for k in SourceKind)
        return f"Неизвестный kind '{kind_raw}'. Допустимые значения: {valid}"

    existing = await repository.list_sources()
    if any(s.identifier == identifier for s in existing):
        return f"Канал '{identifier}' уже есть в списке (см. /list)."

    base_id = slugify_identifier(identifier)
    taken_ids = {s.id for s in existing}
    source_id = base_id
    suffix = 2
    while source_id in taken_ids:
        source_id = f"{base_id}_{suffix}"
        suffix += 1

    source = Source(
        id=source_id,
        kind=kind,
        identifier=identifier,
        display_name=identifier,
        enabled=True,
    )
    added = await repository.add_source(source)
    if not added:
        # Гонка/дубль, не пойманный проверкой выше (например, параллельный
        # /add с тем же identifier) — сообщаем как обычный дубль, а не падаем.
        return f"Канал '{identifier}' уже есть в списке (см. /list)."

    return f"Добавлено: {source.id} — {source.kind.value} ({source.identifier})"


async def cmd_remove(args: list[str], repository: ISourceRepository) -> str:
    """Удаляет канал по id: /remove <id>."""
    if not args:
        return "Использование: /remove <id> (id смотрите в /list)"

    source_id = args[0]
    removed = await repository.remove_source(source_id)
    if removed:
        return f"Удалено: {source_id}"
    return f"Не найден источник с id '{source_id}'. Посмотрите /list"


async def cmd_set_enabled(
    args: list[str], repository: ISourceRepository, *, enabled: bool
) -> str:
    """Включает/выключает канал по id: /pause <id> или /resume <id>."""
    verb = "resume" if enabled else "pause"
    if not args:
        return f"Использование: /{verb} <id> (id смотрите в /list)"

    source_id = args[0]
    changed = await repository.set_enabled(source_id, enabled)
    if not changed:
        return f"Не найден источник с id '{source_id}'. Посмотрите /list"
    return f"{'Включено' if enabled else 'Приостановлено'}: {source_id}"
