"""--validate-config не должен подключаться к Telegram и трогать session-файл.

Регрессия боевого сбоя: валидация в отдельном контейнере рядом с работающим
процессом падала с `sqlite3.OperationalError: database is locked`, потому что
build_pipeline стартовал TelegramClient на той же сессии.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from news_aggregator.main import _async_main, _configure_logging, _RedactingFormatter

TOKEN = "8979586318:AAG0fDoGTZ0xtH4gAd64zCpMwhepJTitcy4"


class _FakeClient:
    """Клиент, который фиксирует вызовы и падает, если его стартуют без разрешения."""

    def __init__(self, allow_start: bool) -> None:
        self._allow_start = allow_start
        self.started = False

    async def start(self) -> None:
        if not self._allow_start:
            raise AssertionError("client.start() вызван, а подключаться нельзя")
        self.started = True


def _telegram_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
sources:
  - "@some_channel"
filters: []
deduplicators:
  - name: text_hash_deduplicator
enrichers: []
publishers:
  - name: telegram_publisher
storage:
  name: sqlite
  params:
    db_path: "{tmp_path / "state.db"}"
""",
        encoding="utf-8",
    )
    return config


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(
        "news_aggregator.sources.telegram_client.build_telegram_client", lambda secrets: client
    )
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.setenv("TELEGRAM_TARGET_CHANNEL", "@target")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)


async def test_validate_config_does_not_start_telegram_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _FakeClient(allow_start=False)
    _patch_client(monkeypatch, client)

    exit_code = await _async_main(
        ["--config", str(_telegram_config(tmp_path)), "--validate-config"]
    )

    assert exit_code == 0
    assert client.started is False


async def test_validate_config_still_checks_secrets_and_target_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_client(monkeypatch, _FakeClient(allow_start=False))
    monkeypatch.delenv("TELEGRAM_TARGET_CHANNEL")

    exit_code = await _async_main(
        ["--config", str(_telegram_config(tmp_path)), "--validate-config"]
    )

    assert exit_code == 1


async def test_regular_run_still_starts_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _FakeClient(allow_start=True)
    _patch_client(monkeypatch, client)

    from news_aggregator.bootstrap import build_registry
    from news_aggregator.config.loader import load_app_config
    from news_aggregator.main import build_pipeline

    await build_pipeline(load_app_config(_telegram_config(tmp_path)), build_registry())

    assert client.started is True


def _format(record_kwargs: dict[str, Any]) -> str:
    formatter = _RedactingFormatter("%(name)s: %(message)s")
    return formatter.format(logging.LogRecord(**record_kwargs))


def _record(msg: str, args: Any = None, exc_info: Any = None) -> dict[str, Any]:
    return {
        "name": "t",
        "level": logging.INFO,
        "pathname": "x",
        "lineno": 1,
        "msg": msg,
        "args": args,
        "exc_info": exc_info,
    }


def test_formatter_redacts_token_in_message_and_args() -> None:
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"

    assert TOKEN not in _format(_record(f"POST {url}"))
    assert TOKEN not in _format(_record("POST %s", (url,)))
    assert "/bot<redacted>/getUpdates" in _format(_record("POST %s", (url,)))


def test_formatter_redacts_token_in_traceback() -> None:
    try:
        raise RuntimeError(f"Client error for url https://api.telegram.org/bot{TOKEN}/getMe")
    except RuntimeError:
        import sys

        text = _format(_record("boom", exc_info=sys.exc_info()))

    assert "RuntimeError" in text
    assert TOKEN not in text


def test_formatter_leaves_normal_text_alone() -> None:
    text = "Опубликовано: https_t_me_exploitex/36948 -> channel, время 10:35:51,316"
    assert _format(_record(text)) == f"t: {text}"


@pytest.mark.parametrize("verbose", [False, True])
def test_configure_logging_silences_httpx_even_in_verbose(verbose: bool) -> None:
    _configure_logging(verbose)

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
