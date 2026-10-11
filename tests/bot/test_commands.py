from datetime import UTC, datetime, timedelta

from news_aggregator.bot.commands import (
    cmd_add,
    cmd_list,
    cmd_remove,
    cmd_set_enabled,
    cmd_stats,
)
from news_aggregator.core.models import SourceKind, StatsSummary
from tests.support.in_memory_source_repository import InMemorySourceRepository


async def test_list_empty_repository() -> None:
    text = await cmd_list(InMemorySourceRepository())
    assert "пуст" in text.lower()


async def test_add_channel_with_default_kind() -> None:
    repo = InMemorySourceRepository()

    text = await cmd_add(["@meduzalive"], repo)

    assert "Добавлено" in text
    sources = await repo.list_sources()
    assert len(sources) == 1
    assert sources[0].id == "meduzalive"
    assert sources[0].kind == SourceKind.TELEGRAM_CHANNEL
    assert sources[0].identifier == "@meduzalive"
    assert sources[0].enabled is True


async def test_add_channel_with_explicit_group_kind() -> None:
    repo = InMemorySourceRepository()

    text = await cmd_add(["@some_group", "telegram_group"], repo)

    assert "Добавлено" in text
    sources = await repo.list_sources()
    assert sources[0].kind == SourceKind.TELEGRAM_GROUP


async def test_add_without_args_shows_usage() -> None:
    text = await cmd_add([], InMemorySourceRepository())
    assert "Использование" in text


async def test_add_with_invalid_kind_reports_valid_options() -> None:
    text = await cmd_add(["@x", "not_a_real_kind"], InMemorySourceRepository())
    assert "Неизвестный kind" in text
    assert "telegram_channel" in text


async def test_add_duplicate_identifier_is_rejected() -> None:
    repo = InMemorySourceRepository()
    await cmd_add(["@news"], repo)

    text = await cmd_add(["@news"], repo)

    assert "уже есть в списке" in text
    assert len(await repo.list_sources()) == 1


async def test_add_resolves_id_collision_with_suffix() -> None:
    repo = InMemorySourceRepository()
    await cmd_add(["@news"], repo)  # id станет "news"

    # другой канал, чей slug тоже "news" (после нормализации префикса)
    await cmd_add(["news"], repo)

    ids = [s.id for s in await repo.list_sources()]
    assert ids == ["news", "news_2"]


async def test_list_shows_added_channel_with_status_marker() -> None:
    repo = InMemorySourceRepository()
    await cmd_add(["@meduzalive"], repo)

    text = await cmd_list(repo)

    assert "meduzalive" in text
    assert "🟢" in text


async def test_remove_existing_channel() -> None:
    repo = InMemorySourceRepository()
    await cmd_add(["@news"], repo)

    text = await cmd_remove(["news"], repo)

    assert "Удалено" in text
    assert await repo.list_sources() == []


async def test_remove_missing_channel_reports_not_found() -> None:
    text = await cmd_remove(["missing"], InMemorySourceRepository())
    assert "Не найден" in text


async def test_remove_without_args_shows_usage() -> None:
    text = await cmd_remove([], InMemorySourceRepository())
    assert "Использование" in text


async def test_pause_and_resume_channel() -> None:
    repo = InMemorySourceRepository()
    await cmd_add(["@news"], repo)

    pause_text = await cmd_set_enabled(["news"], repo, enabled=False)
    assert "Приостановлено" in pause_text
    source = await repo.get_source("news")
    assert source is not None
    assert source.enabled is False

    resume_text = await cmd_set_enabled(["news"], repo, enabled=True)
    assert "Включено" in resume_text
    source = await repo.get_source("news")
    assert source is not None
    assert source.enabled is True


async def test_pause_missing_channel_reports_not_found() -> None:
    text = await cmd_set_enabled(["missing"], InMemorySourceRepository(), enabled=False)
    assert "Не найден" in text


async def test_pause_without_args_shows_usage_with_correct_verb() -> None:
    text = await cmd_set_enabled([], InMemorySourceRepository(), enabled=False)
    assert "/pause" in text

    text2 = await cmd_set_enabled([], InMemorySourceRepository(), enabled=True)
    assert "/resume" in text2


# ---------------------------------------------------------------- /stats

_NOW = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)


class _FakeStatsStore:
    """Двойник IRunStatsStore: запоминает запрошенное окно и отдаёт готовую сводку."""

    def __init__(self, summary: StatsSummary) -> None:
        self._summary = summary
        self.requested_since: datetime | None = None

    async def summarize(self, since: datetime) -> StatsSummary:
        self.requested_since = since
        return self._summary


def _summary(**overrides: object) -> StatsSummary:
    base: dict[str, object] = {
        "runs": 60,
        "messages_fetched": 12,
        "messages_filtered_out": 3,
        "messages_duplicate": 4,
        "messages_published": 5,
        "runs_with_failures": 0,
        "sources_failed_total": 0,
        "last_run_at": _NOW - timedelta(seconds=30),
    }
    base.update(overrides)
    return StatsSummary(**base)  # type: ignore[arg-type]


async def test_stats_without_store_says_not_enabled() -> None:
    text = await cmd_stats([], None)
    assert "не включена" in text


async def test_stats_default_window_is_one_hour() -> None:
    store = _FakeStatsStore(_summary())

    text = await cmd_stats([], store, now=_NOW)  # type: ignore[arg-type]

    assert store.requested_since == _NOW - timedelta(hours=1)
    assert "последние 1 ч" in text


async def test_stats_shows_all_counters() -> None:
    text = await cmd_stats([], _FakeStatsStore(_summary()), now=_NOW)  # type: ignore[arg-type]

    assert "Прочитано: 12" in text
    assert "Отфильтровано: 3" in text
    assert "Дублей: 4" in text
    assert "Опубликовано: 5" in text
    assert "прогонов: 60" in text


async def test_stats_custom_window_in_hours() -> None:
    store = _FakeStatsStore(_summary())

    text = await cmd_stats(["24"], store, now=_NOW)  # type: ignore[arg-type]

    assert store.requested_since == _NOW - timedelta(hours=24)
    assert "последние 24 ч" in text


async def test_stats_accepts_comma_decimal_and_fractional_window() -> None:
    store = _FakeStatsStore(_summary())

    text = await cmd_stats(["0,5"], store, now=_NOW)  # type: ignore[arg-type]

    assert store.requested_since == _NOW - timedelta(minutes=30)
    assert "последние 30 мин" in text


async def test_stats_reports_failures_when_present() -> None:
    summary = _summary(runs_with_failures=4, sources_failed_total=9)

    text = await cmd_stats([], _FakeStatsStore(summary), now=_NOW)  # type: ignore[arg-type]

    assert "4 из 60" in text
    assert "9" in text
    assert "Сбоев источников нет" not in text


async def test_stats_says_no_failures_when_clean() -> None:
    text = await cmd_stats([], _FakeStatsStore(_summary()), now=_NOW)  # type: ignore[arg-type]

    assert "Сбоев источников нет" in text


async def test_stats_with_no_runs_hints_to_check_process() -> None:
    summary = _summary(runs=0, last_run_at=None)

    text = await cmd_stats([], _FakeStatsStore(summary), now=_NOW)  # type: ignore[arg-type]

    assert "прогонов не было" in text
    assert "docker compose" in text


async def test_stats_shows_age_of_last_run() -> None:
    summary = _summary(last_run_at=_NOW - timedelta(minutes=7))

    text = await cmd_stats([], _FakeStatsStore(summary), now=_NOW)  # type: ignore[arg-type]

    assert "Последний прогон: 7 мин назад" in text


async def test_stats_rejects_garbage_argument() -> None:
    store = _FakeStatsStore(_summary())

    text = await cmd_stats(["abc"], store, now=_NOW)  # type: ignore[arg-type]

    assert "Использование" in text
    assert store.requested_since is None  # в хранилище не ходили


async def test_stats_rejects_out_of_range_windows() -> None:
    store = _FakeStatsStore(_summary())

    for bad in ("0", "-3", "169", "1000"):
        text = await cmd_stats([bad], store, now=_NOW)  # type: ignore[arg-type]
        assert "от 0 до" in text, bad

    assert store.requested_since is None


async def test_stats_accepts_exactly_max_window() -> None:
    store = _FakeStatsStore(_summary())

    await cmd_stats(["168"], store, now=_NOW)  # type: ignore[arg-type]

    assert store.requested_since == _NOW - timedelta(hours=168)
