"""Обработчик /stats в настоящем AggregatorBot (python-telegram-bot), без сети.

Application строится, но не запускается: хендлеры вызываются напрямую с
лёгкими двойниками Update/Context — то же, что делает сам PTB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from news_aggregator.bot.telegram_bot import AggregatorBot
from news_aggregator.core.models import StatsSummary
from tests.support.in_memory_source_repository import InMemorySourceRepository

TOKEN = "123456:" + "A" * 35
OWNER = 42


class _Store:
    def __init__(self) -> None:
        self.calls = 0

    async def summarize(self, since: datetime) -> StatsSummary:
        self.calls += 1
        return StatsSummary(
            runs=3, messages_published=7, last_run_at=datetime.now(UTC) - timedelta(seconds=5)
        )


class _Message:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


def _update(user_id: int | None) -> Any:
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    return SimpleNamespace(effective_user=user, message=_Message())


def _bot(store: _Store | None) -> AggregatorBot:
    return AggregatorBot(TOKEN, OWNER, InMemorySourceRepository(), stats_store=store)  # type: ignore[arg-type]


async def test_owner_gets_stats() -> None:
    store = _Store()
    update = _update(OWNER)

    await _bot(store)._handle_stats(update, SimpleNamespace(args=[]))

    assert store.calls == 1
    assert "Опубликовано: 7" in update.message.replies[0]


async def test_owner_can_pass_window_argument() -> None:
    update = _update(OWNER)

    await _bot(_Store())._handle_stats(update, SimpleNamespace(args=["6"]))

    assert "последние 6 ч" in update.message.replies[0]


async def test_stranger_is_rejected_and_store_not_queried() -> None:
    store = _Store()
    update = _update(999)

    await _bot(store)._handle_stats(update, SimpleNamespace(args=[]))

    assert store.calls == 0
    assert "только владельцу" in update.message.replies[0]


async def test_update_without_user_is_rejected() -> None:
    update = _update(None)

    await _bot(_Store())._handle_stats(update, SimpleNamespace(args=[]))

    assert "только владельцу" in update.message.replies[0]


async def test_bot_without_store_reports_stats_disabled() -> None:
    update = _update(OWNER)

    await _bot(None)._handle_stats(update, SimpleNamespace(args=[]))

    assert "не включена" in update.message.replies[0]


def test_stats_command_is_registered() -> None:
    bot = _bot(_Store())

    registered = {c for h in bot._app.handlers[0] for c in getattr(h, "commands", [])}

    assert "stats" in registered
