"""Telegram-бот управления списком каналов (python-telegram-bot, Bot API).

Отдельная сущность от Telethon (MTProto), который читает каналы, — этот
бот только принимает команды от владельца и правит ISourceRepository.
Вся бизнес-логика вынесена в bot/commands.py; здесь только тонкая обвязка
над Update/Application из python-telegram-bot.

Персональный инструмент: отвечает только пользователю с id == owner_id,
остальным — вежливый отказ. Никакого группового/многопользовательского
доступа не подразумевается.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from news_aggregator.bot.commands import USAGE, cmd_add, cmd_list, cmd_remove, cmd_set_enabled
from news_aggregator.core.interfaces import ISourceRepository

logger = logging.getLogger(__name__)

_UNAUTHORIZED_REPLY = "Эта команда доступна только владельцу бота."


class AggregatorBot:
    """Long-polling Telegram-бот для команд /list, /add, /remove, /pause, /resume."""

    def __init__(self, token: str, owner_id: int, repository: ISourceRepository) -> None:
        self._owner_id = owner_id
        self._repository = repository
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

    async def _handle_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            await self._reply(update, _UNAUTHORIZED_REPLY)
            return
        text = await cmd_set_enabled(list(context.args or []), self._repository, enabled=True)
        await self._reply(update, text)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Запускает long-polling; блокируется до срабатывания stop_event."""
        await self._app.initialize()
        await self._app.start()
        assert self._app.updater is not None
        await self._app.updater.start_polling()
        logger.info("Бот управления каналами запущен (long polling)")
        try:
            await stop_event.wait()
        finally:
            logger.info("Останавливаю бота управления каналами...")
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
