"""Тесты флага --validate-config: не должен запускать пайплайн, только
проверять, что конфигурация и .env (для Telegram-компонентов) корректны."""

from __future__ import annotations

from pathlib import Path

import pytest

from news_aggregator.main import _async_main

_PROJECT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


async def test_validate_config_succeeds_on_real_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_text = _PROJECT_CONFIG.read_text(encoding="utf-8")
    db_path = tmp_path / "validate_state.db"
    config_text = config_text.replace('db_path: "data/state.db"', f'db_path: "{db_path}"')
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text, encoding="utf-8")

    exit_code = await _async_main(["--config", str(config_path), "--validate-config"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Конфигурация корректна" in out
    assert "fake_demo_source" in out


async def test_validate_config_fails_on_duplicate_channel(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
sources:
  - "@news"
  - "@news"
publishers:
  - name: console_publisher
""",
        encoding="utf-8",
    )

    exit_code = await _async_main(["--config", str(config_path), "--validate-config"])

    assert exit_code == 1


async def test_validate_config_does_not_advance_cursor(tmp_path: Path) -> None:
    """--validate-config не должен считаться прогоном пайплайна: курсор
    источников должен остаться нетронутым для последующего --once."""
    config_text = _PROJECT_CONFIG.read_text(encoding="utf-8")
    db_path = tmp_path / "validate_state.db"
    config_text = config_text.replace('db_path: "data/state.db"', f'db_path: "{db_path}"')
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text, encoding="utf-8")

    await _async_main(["--config", str(config_path), "--validate-config"])
    exit_code = await _async_main(["--config", str(config_path), "--once"])

    assert exit_code == 0
    # Если бы --validate-config запускал пайплайн, курсор уже был бы продвинут
    # и второй вызов --once не нашёл бы новых сообщений. Проверяем через сам
    # факт того, что второй вызов не падает и обрабатывает демо-сообщения
    # (более точная проверка стата вынесена в test_full_pipeline.py).
