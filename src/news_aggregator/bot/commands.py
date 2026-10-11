"""Бизнес-логика команд Telegram-бота управления списком каналов.

Каждая функция принимает уже распарсенные аргументы команды и
ISourceRepository, а возвращает готовый текст ответа — без единого
упоминания python-telegram-bot. Поэтому весь модуль полностью
тестируется без установленной библиотеки бота; bot/telegram_bot.py —
тонкая обвязка, которая только достаёт аргументы из Update/Context и
вызывает эти функции.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from news_aggregator.config.schema import slugify_identifier
from news_aggregator.core.interfaces import IRunStatsStore, ISourceRepository
from news_aggregator.core.models import Source, SourceKind

USAGE = (
    "Команды:\n"
    "/list — показать все каналы\n"
    "/add <identifier> [kind] — добавить канал "
    "(kind: telegram_channel по умолчанию, или telegram_group)\n"
    "/remove <id> — удалить канал\n"
    "/pause <id> — временно выключить канал (без удаления)\n"
    "/resume <id> — снова включить канал\n"
    "/stats [часов] — статистика прогонов (по умолчанию за последний час)"
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


async def cmd_set_enabled(args: list[str], repository: ISourceRepository, *, enabled: bool) -> str:
    """Включает/выключает канал по id: /pause <id> или /resume <id>."""
    verb = "resume" if enabled else "pause"
    if not args:
        return f"Использование: /{verb} <id> (id смотрите в /list)"

    source_id = args[0]
    changed = await repository.set_enabled(source_id, enabled)
    if not changed:
        return f"Не найден источник с id '{source_id}'. Посмотрите /list"
    return f"{'Включено' if enabled else 'Приостановлено'}: {source_id}"


DEFAULT_STATS_HOURS = 1.0
MAX_STATS_HOURS = 168.0  # неделя — столько хранится история прогонов


def _format_age(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds} с назад"
    if seconds < 3600:
        return f"{seconds // 60} мин назад"
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours} ч {minutes} мин назад"


def _format_window(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)} мин"
    if hours == int(hours):
        return f"{int(hours)} ч"
    return f"{hours:g} ч"


async def cmd_stats(
    args: list[str], stats_store: IRunStatsStore | None, *, now: datetime | None = None
) -> str:
    """Сводка по прогонам пайплайна за окно времени: /stats [часов]."""
    if stats_store is None:
        return "Статистика не включена: хранилище прогонов недоступно."

    hours = DEFAULT_STATS_HOURS
    if args:
        try:
            hours = float(args[0].replace(",", "."))
        except ValueError:
            return f"Не понял «{args[0]}». Использование: /stats [часов], например /stats 24"
        if not 0 < hours <= MAX_STATS_HOURS:
            return f"Окно должно быть от 0 до {MAX_STATS_HOURS:g} часов (хранится неделя)."

    moment = now or datetime.now(UTC)
    summary = await stats_store.summarize(moment - timedelta(hours=hours))
    window = _format_window(hours)

    if summary.runs == 0:
        return (
            f"За последние {window} прогонов не было.\n"
            "Если процесс должен работать — проверьте его: docker compose ps / logs."
        )

    lines = [
        f"Статистика за последние {window} (прогонов: {summary.runs}):",
        f"📥 Прочитано: {summary.messages_fetched}",
        f"🚫 Отфильтровано: {summary.messages_filtered_out}",
        f"♻️ Дублей: {summary.messages_duplicate}",
        f"✅ Опубликовано: {summary.messages_published}",
    ]
    if summary.runs_with_failures:
        lines.append(
            f"⚠️ Прогонов с упавшими источниками: {summary.runs_with_failures} "
            f"из {summary.runs} (всего падений источников: {summary.sources_failed_total})"
        )
    else:
        lines.append("Сбоев источников нет.")
    if summary.last_run_at is not None:
        lines.append(f"Последний прогон: {_format_age(moment - summary.last_run_at)}")
    return "\n".join(lines)
