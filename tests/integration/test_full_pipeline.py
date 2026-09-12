"""Интеграционный тест полного цикла.

Проверяет весь путь: чтение реального config.yaml проекта -> сборка
ComponentRegistry -> сборка AggregatorPipeline -> один прогон с
FakeSourceReader и ConsolePublisher, без реального Telegram и без
использования каких-либо внешних сервисов.
"""

from __future__ import annotations

from pathlib import Path

from news_aggregator.bootstrap import build_registry
from news_aggregator.config.loader import load_app_config
from news_aggregator.main import build_pipeline

_PROJECT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


async def test_full_pipeline_from_real_config_dry_run(tmp_path: Path) -> None:
    config_text = _PROJECT_CONFIG.read_text(encoding="utf-8")
    db_path = tmp_path / "integration_state.db"
    config_text = config_text.replace(
        'db_path: "data/state.db"', f'db_path: "{db_path}"'
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text, encoding="utf-8")

    config = load_app_config(config_path)
    registry = build_registry()
    pipeline, _ = await build_pipeline(config, registry)

    stats = await pipeline.run_once()

    # 6 демо-сообщений: 1 дубль по ссылке, 1 реклама, 1 короткое -> 3 публикуются
    assert stats.messages_fetched == 6
    assert stats.messages_duplicate == 1
    assert stats.messages_filtered_out == 2
    assert stats.messages_published == 3
    assert stats.sources_failed == 0
    assert db_path.exists()


async def test_second_run_has_nothing_new_due_to_cursor(tmp_path: Path) -> None:
    config_text = _PROJECT_CONFIG.read_text(encoding="utf-8")
    db_path = tmp_path / "integration_state.db"
    config_text = config_text.replace(
        'db_path: "data/state.db"', f'db_path: "{db_path}"'
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text, encoding="utf-8")

    config = load_app_config(config_path)
    registry = build_registry()

    pipeline1, _ = await build_pipeline(config, registry)
    await pipeline1.run_once()

    pipeline2, _ = await build_pipeline(config, registry)
    stats2 = await pipeline2.run_once()

    assert stats2.messages_fetched == 0
    assert stats2.messages_published == 0


async def test_real_publish_mode_actually_invokes_publisher(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    config_text = _PROJECT_CONFIG.read_text(encoding="utf-8")
    db_path = tmp_path / "integration_state.db"
    config_text = config_text.replace(
        'db_path: "data/state.db"', f'db_path: "{db_path}"'
    )
    config_text = config_text.replace("dry_run: true", "dry_run: false")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text, encoding="utf-8")

    config = load_app_config(config_path)
    registry = build_registry()
    pipeline, _ = await build_pipeline(config, registry)

    stats = await pipeline.run_once()

    assert stats.messages_published == 3


async def test_channel_added_via_repository_is_picked_up_without_rebuilding_pipeline(
    tmp_path: Path,
) -> None:
    """Полный путь, моделирующий то, что делает бот через /add: канал
    добавляется напрямую в ISourceRepository, а не в config.yaml, и должен
    быть подхвачен следующим run_once() без пересборки пайплайна."""
    config_text = _PROJECT_CONFIG.read_text(encoding="utf-8")
    db_path = tmp_path / "integration_state.db"
    config_text = config_text.replace(
        'db_path: "data/state.db"', f'db_path: "{db_path}"'
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text, encoding="utf-8")

    config = load_app_config(config_path)
    registry = build_registry()
    pipeline, source_repository = await build_pipeline(config, registry)

    stats_before = await pipeline.run_once()
    assert stats_before.sources_total == 1  # только исходный fake_demo_source

    from news_aggregator.core.models import Source, SourceKind

    added = await source_repository.add_source(
        Source(
            id="added_via_bot",
            kind=SourceKind.FAKE,
            identifier="added_via_bot",
            display_name="Добавлено через /add",
        )
    )
    assert added is True

    stats_after = await pipeline.run_once()

    assert stats_after.sources_total == 2
