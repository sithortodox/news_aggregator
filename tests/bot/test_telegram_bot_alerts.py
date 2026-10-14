"""AggregatorBot как получатель оповещений (IAlertSink), на настоящем PTB без сети."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from telegram.ext import Application, Updater

from news_aggregator.bot.telegram_bot import AggregatorBot
from news_aggregator.core.interfaces import IAlertSink
from tests.support.in_memory_source_repository import InMemorySourceRepository

TOKEN = "123456:" + "A" * 35
OWNER = 42


def _bot(**kwargs: Any) -> AggregatorBot:
    return AggregatorBot(TOKEN, OWNER, InMemorySourceRepository(), **kwargs)


def _capture_send_message(
    monkeypatch: pytest.MonkeyPatch, bot: AggregatorBot
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_send_message(self: Any, *args: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(type(bot._app.bot), "send_message", fake_send_message)
    return calls


def test_bot_is_an_alert_sink() -> None:
    assert isinstance(_bot(), IAlertSink)


async def test_send_delivers_to_owner_private_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _bot()
    calls = _capture_send_message(monkeypatch, bot)
    bot._ready.set()

    await bot.send("⚠️ источник не отвечает")

    assert calls == [{"chat_id": OWNER, "text": "⚠️ источник не отвечает"}]


async def test_send_truncates_text_over_telegram_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _bot()
    calls = _capture_send_message(monkeypatch, bot)
    bot._ready.set()

    await bot.send("x" * 6000)

    assert len(calls[0]["text"]) <= 4096


async def test_send_before_bot_is_ready_fails_with_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = _bot(ready_timeout_seconds=0.05)
    calls = _capture_send_message(monkeypatch, bot)

    with pytest.raises(RuntimeError, match="ещё не запущен"):
        await bot.send("рано")

    assert calls == []


async def test_send_waits_for_bot_to_become_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _bot(ready_timeout_seconds=5)
    calls = _capture_send_message(monkeypatch, bot)

    task = asyncio.create_task(bot.send("дождись старта"))
    await asyncio.sleep(0.02)
    assert not task.done()
    assert calls == []

    bot._ready.set()
    await asyncio.wait_for(task, timeout=1)

    assert len(calls) == 1


async def test_send_propagates_delivery_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = _bot()
    bot._ready.set()

    async def broken(self: Any, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("telegram недоступен")

    monkeypatch.setattr(type(bot._app.bot), "send_message", broken)

    with pytest.raises(ConnectionError):
        await bot.send("текст")


async def test_run_marks_ready_after_polling_starts_and_clears_it_on_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def stub(name: str) -> Any:
        async def _stub(self: Any, *args: Any, **kwargs: Any) -> None:
            # Как настоящий сетевой вызов, уступает управление циклу событий —
            # иначе порядок шагов нельзя наблюдать снаружи.
            await asyncio.sleep(0)
            events.append(name)

        return _stub

    monkeypatch.setattr(Application, "initialize", stub("initialize"))
    monkeypatch.setattr(Application, "start", stub("start"))
    monkeypatch.setattr(Application, "stop", stub("stop"))
    monkeypatch.setattr(Application, "shutdown", stub("shutdown"))
    monkeypatch.setattr(Updater, "start_polling", stub("start_polling"))
    monkeypatch.setattr(Updater, "stop", stub("updater_stop"))

    bot = _bot()
    stop_event = asyncio.Event()
    run_task = asyncio.create_task(bot.run(stop_event))

    await asyncio.wait_for(bot._ready.wait(), timeout=1)
    assert events == ["initialize", "start", "start_polling"]  # готов только после старта polling

    stop_event.set()
    await asyncio.wait_for(run_task, timeout=1)

    assert not bot._ready.is_set()
    assert events[-3:] == ["updater_stop", "stop", "shutdown"]
