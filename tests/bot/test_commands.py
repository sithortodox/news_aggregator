from news_aggregator.bot.commands import cmd_add, cmd_list, cmd_remove, cmd_set_enabled
from news_aggregator.core.models import SourceKind
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
