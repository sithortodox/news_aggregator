"""Telegram-бот управления списком каналов (python-telegram-bot, Bot API).

Отдельная сущность от Telethon (MTProto), который читает каналы, — этот
бот только принимает команды от владельца и правит ISourceRepository.
Вся бизнес-логика вынесена в bot/commands.py; здесь только тонкая обвязка
над Update/Application из python-telegram-bot.

Персональный инструмент: отвечает только пользователю с id == owner_id,
остальным — вежливый отказ. Никакого группового/многопользовательского
доступа не подразумевается.

Он же — получатель оповещений о сбоях (IAlertSink): send() пишет владельцу
в личные сообщения. Telegram не даёт боту писать первым, поэтому владелец
должен один раз отправить боту /start.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from news_aggregator.bot.commands import (
    USAGE,
    cmd_add,
    cmd_list,
    cmd_remove,
    cmd_set_enabled,
    cmd_stats,
)
from news_aggregator.core.interfaces import IAlertSink, IRunStatsStore, ISourceRepository

logger = logging.getLogger(__name__)

_UNAUTHORIZED_REPLY = "Эта команда доступна только владельцу бота."


class AggregatorBot(IAlertSink):
    """Long-polling Telegram-бот: /list, /add, /remove, /pause, /resume, /stats."""

    def __init__(
        self,
        token: str,
        owner_id: int,
        repository: ISourceRepository,
        stats_store: IRunStatsStore | None = None,
        *,
        ready_timeout_seconds: float = 30.0,
    ) -> None:
        self._owner_id = owner_id
        # Выставляется, когда Application инициализирован и бот может слать запросы.
        self._ready = asyncio.Event()
        self._ready_timeout = ready_timeout_seconds
        self._repository = repository
        self._stats_store = stats_store
        self._app: Application[Any, Any, Any, Any, Any, Any] = (
            Application.builder().token(token).build()
        )
        self._register_handlers()

    def _register_handlers(self) -> None:
        self._app.add_handler(CommandHandler(["start", "help"], self._handle_start))
        self._app.add_handler(CommandHandler("list", self._handle_list))
        self._app.add_handler(CommandHandler("add", self._handle_add))
        self._app.add_handler(CommandHandler("remove", self._handle_remove))
        self._app.add_handler(CommandHandler("pause", self._handle_pause))
        self._app.add_handler(CommandHandler("resume", self._handle_resume))
        self._app.add_handler(CommandHandler("stats", self._handle_stats))

    def _is_owner(self, update: Update) -> bool:
        return update.effective_user is not None and update.effective_user.id == self._owner_id

    async def _reply(self, update: Update, text: str) -> None:
        if update.message is not None:
            await update.message.reply_text(text)

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            await self._reply(update, _UNAUTHORIZED_REPLY)
            return
        await self._reply(update, "Бот управления каналами news-aggregator.\n\n" + USAGE)

    async def _handle_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            await self._reply(update, _UNAUTHORIZED_REPLY)
            return
        await self._reply(update, await cmd_list(self._repository))

    async def _handle_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            await self._reply(update, _UNAUTHORIZED_REPLY)
            return
        await self._reply(update, await cmd_add(list(context.args or []), self._repository))

    async def _handle_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            await self._reply(update, _UNAUTHORIZED_REPLY)
            return
        await self._reply(update, await cmd_remove(list(context.args or []), self._repository))

    async def _handle_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            await self._reply(update, _UNAUTHORIZED_REPLY)
            return
        text = await cmd_set_enabled(list(context.args or []), self._repository, enabled=False)
        await self._reply(update, text)

    async def _handle_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            await self._reply(update, _UNAUTHORIZED_REPLY)
            return
        await self._reply(update, await cmd_stats(list(context.args or []), self._stats_store))

    async def _handle_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            await self._reply(update, _UNAUTHORIZED_REPLY)
            return
        text = await cmd_set_enabled(list(context.args or []), self._repository, enabled=True)
        await self._reply(update, text)

    async def send(self, text: str) -> None:
        """Отправляет оповещение владельцу в личные сообщения (IAlertSink).

        Если бот ещё не стартовал, ждёт до ready_timeout_seconds — оповещение
        на самом первом прогоне может опередить запуск long polling.
        """
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self._ready_timeout)
        except TimeoutError as exc:
            raise RuntimeError("бот управления ещё не запущен, оповещение не отправлено") from exc
        # Лимит Telegram — 4096 символов; оповещения короткие, но защищаемся.
        await self._app.bot.send_message(chat_id=self._owner_id, text=text[:4000])

    async def run(self, stop_event: asyncio.Event) -> None:
        """Запускает long-polling; блокируется до срабатывания stop_event."""
        await self._app.initialize()
        await self._app.start()
        assert self._app.updater is not None
        await self._app.updater.start_polling()
        self._ready.set()
        logger.info("Бот управления каналами запущен (long polling)")
        try:
            await stop_event.wait()
        finally:
            self._ready.clear()
            logger.info("Останавливаю бота управления каналами...")
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
