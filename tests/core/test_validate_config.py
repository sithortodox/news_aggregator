"""Тесты флага --validate-config: не должен запускать пайплайн, только
проверять, что конфигурация и .env (для Telegram-компонентов) корректны."""

from __future__ import annotations

from pathlib import Path

import pytest

from news_aggregator.main import _async_main

# Конфиг для тестов --validate-config: фейковый источник вместо живых
# Telegram-каналов (как было в реальном config.yaml до перехода на живые
# источники). Без фейка в CI не хватает Telegram-ключей и демо-сообщений.
_FAKE_SOURCE_CONFIG = """\
pipeline:
  dry_run: true
  poll_interval_seconds: 60
  max_messages_per_source: 100

alerts:
  enabled: true
  source_failure_threshold: 5
  publish_failure_threshold: 1
  flood_wait_alert_seconds: 300
  cooldown_seconds: 3600

sources:
  - identifier: "fake_demo_source"
    kind: fake
    display_name: "Fake Demo"

filters: []

deduplicators: []

enrichers: []

publishers:
  - name: console_publisher

storage:
  name: sqlite
  params:
    db_path: "data/state.db"
"""


def _write_fake_config(
    tmp_path: Path,
    *,
    source_failure_threshold: int = 5,
    alerts_enabled: bool = True,
) -> Path:
    config_text = _FAKE_SOURCE_CONFIG
    config_text = config_text.replace(
        'db_path: "data/state.db"', f'db_path: "{tmp_path / "state.db"}"'
    )
    config_text = config_text.replace(
        "source_failure_threshold: 5", f"source_failure_threshold: {source_failure_threshold}"
    )
    config_text = config_text.replace(
        "alerts:\n  enabled: true", f"alerts:\n  enabled: {'true' if alerts_enabled else 'false'}"
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    return config_path


async def test_validate_config_succeeds_on_fake_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _write_fake_config(tmp_path)

    exit_code = await _async_main(["--config", str(config_path), "--validate-config"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Конфигурация корректна" in out
    assert "fake_demo_source" in out


async def test_validate_config_fails_on_duplicate_channel(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        'sources:\n  - "@news"\n  - "@news"\npublishers:\n  - name: console_publisher\n',
        encoding="utf-8",
    )

    assert await _async_main(["--config", str(config_path), "--validate-config"]) == 1


async def test_validate_config_does_not_advance_cursor(tmp_path: Path) -> None:
    """--validate-config не должен считаться прогоном пайплайна: курсор
    источников должен остаться нетронутым для последующего --once."""
    config_path = _write_fake_config(tmp_path)

    await _async_main(["--config", str(config_path), "--validate-config"])
    exit_code = await _async_main(["--config", str(config_path), "--once"])

    assert exit_code == 0


async def test_validate_config_prints_alert_settings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _write_fake_config(tmp_path, source_failure_threshold=7)

    exit_code = await _async_main(["--config", str(config_path), "--validate-config"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Оповещения: включены" in out
    assert "источник: 7 подряд" in out


async def test_validate_config_reports_disabled_alerts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = _write_fake_config(tmp_path, alerts_enabled=False)

    await _async_main(["--config", str(config_path), "--validate-config"])

    assert "Оповещения: выключены" in capsys.readouterr().out


async def test_validate_config_fails_on_alerts_typo(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "sources: []\npublishers:\n  - name: console_publisher\nalerts:\n  cooldown_second: 5\n",
        encoding="utf-8",
    )

    assert await _async_main(["--config", str(config_path), "--validate-config"]) == 1
