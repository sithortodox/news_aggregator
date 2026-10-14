"""Оповещения о сбоях: источник недоступен, не публикуется, FloodWait.

AlertManager смотрит на PipelineStats каждого прогона и решает, пора ли
оповестить владельца. Он ничего не знает про Telegram — доставка идёт через
IAlertSink (в проекте это управляющий бот).

Принцип против спама: **одно оповещение на эпизод проблемы**, а не на каждый
прогон. Пока источник недоступен, владелец получает «не отвечает» один раз и
один раз «снова работает». Дополнительно действует cooldown: если проблема
«мигает» (упал, ожил, снова упал), повторное оповещение о проблеме по этому же
ключу не приходит чаще, чем раз в cooldown_seconds, — а раз «упал» не
отправили, парное «ожил» тоже не отправляется. Если подавленная проблема
сохраняется, оповещение придёт, как только cooldown истечёт.

Состояние хранится в памяти: после перезапуска процесса уже существующая
проблема даст одно новое оповещение, когда снова наберёт порог.

Любое оповещение дополнительно пишется в лог (WARNING), так что при
выключенном или недоступном боте проблема не остаётся совсем незамеченной.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from news_aggregator.config.schema import AlertsConfig
from news_aggregator.core.interfaces import IAlertSink
from news_aggregator.core.models import PipelineStats
from news_aggregator.redaction import redact_secrets

logger = logging.getLogger(__name__)

_KEY_PUBLISHER = "publisher"
_KEY_FLOOD = "flood"


def _source_key(source_id: str) -> str:
    return f"source:{source_id}"


def format_duration(delta: timedelta) -> str:
    """Человекочитаемая длительность: «45 с», «7 мин», «1 ч 20 мин»."""
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds} с"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин"
    hours, rest = divmod(minutes, 60)
    return f"{hours} ч {rest} мин" if rest else f"{hours} ч"


def runs_phrase(count: int) -> str:
    """«1 прогон», «3 прогона», «5 прогонов», «21 прогон», «12 прогонов»."""
    last_two, last = count % 100, count % 10
    if last == 1 and last_two != 11:
        word = "прогон"
    elif 2 <= last <= 4 and not 12 <= last_two <= 14:
        word = "прогона"
    else:
        word = "прогонов"
    return f"{count} {word}"


class AlertManager:
    """Превращает статистику прогонов в оповещения (см. описание модуля)."""

    def __init__(
        self,
        config: AlertsConfig,
        sink: IAlertSink | None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._sink = sink
        self._clock = clock
        self._cooldown = timedelta(seconds=config.cooldown_seconds)

        self._source_failures: dict[str, int] = {}
        # Когда по источнику/издателю было отправлено оповещение «проблема» —
        # то есть владелец знает, что что-то сломано, и ему стоит сообщить о починке.
        self._source_alerted_at: dict[str, datetime] = {}
        self._publish_failure_runs = 0
        self._publisher_alerted_at: datetime | None = None
        self._last_sent: dict[str, datetime] = {}

    async def observe(self, stats: PipelineStats) -> None:
        """Обрабатывает итоги одного прогона."""
        if not self._config.enabled:
            return
        await self._observe_sources(stats)
        await self._observe_publisher(stats)
        await self._observe_flood(stats)

    # ------------------------------------------------------------------ источники

    async def _observe_sources(self, stats: PipelineStats) -> None:
        for source_id in stats.failed_sources:
            failures = self._source_failures.get(source_id, 0) + 1
            self._source_failures[source_id] = failures
            if failures < self._config.source_failure_threshold:
                continue
            if source_id in self._source_alerted_at:
                continue
            error = stats.source_errors.get(source_id, "причина неизвестна")
            sent = await self._send(
                _source_key(source_id),
                f"⚠️ Источник «{source_id}» не отвечает: {runs_phrase(failures)} подряд с ошибкой.\n"
                f"Последняя ошибка: {error}\n"
                "Сообщу, когда он снова заработает.",
            )
            if sent:
                self._source_alerted_at[source_id] = self._clock()

        # Источники, которые в этом прогоне не опрашивались (пауза из-за FloodWait,
        # выключены), в обе стороны нейтральны: счётчик не растёт и не сбрасывается.
        for source_id in stats.succeeded_sources:
            self._source_failures.pop(source_id, None)
            alerted_at = self._source_alerted_at.pop(source_id, None)
            if alerted_at is not None:
                lasted = format_duration(self._clock() - alerted_at)
                sent = await self._send(
                    _source_key(source_id),
                    f"✅ Источник «{source_id}» снова работает (проблема длилась ~{lasted}).",
                    announce_recovery=True,
                )
                if not sent:
                    # Не доставили — иначе владелец навсегда останется с «не отвечает».
                    self._source_alerted_at[source_id] = alerted_at

    # ------------------------------------------------------------------- издатель

    async def _observe_publisher(self, stats: PipelineStats) -> None:
        if stats.publish_failures > 0:
            self._publish_failure_runs += 1
            if (
                self._publish_failure_runs >= self._config.publish_failure_threshold
                and self._publisher_alerted_at is None
            ):
                error = stats.publish_error or "причина неизвестна"
                sent = await self._send(
                    _KEY_PUBLISHER,
                    f"🚨 Не удаётся публиковать посты: "
                    f"{runs_phrase(self._publish_failure_runs)} подряд с ошибкой публикации.\n"
                    f"Последняя ошибка: {error}\n"
                    "Неопубликованные посты автоматически не повторяются. "
                    "Сообщу, когда публикация восстановится.",
                )
                if sent:
                    self._publisher_alerted_at = self._clock()
            return

        # Доказательство, что публикация работает, — реально опубликованный пост.
        # Прогон без постов ничего не доказывает и состояние не меняет.
        if stats.messages_published > 0:
            self._publish_failure_runs = 0
            alerted_at, self._publisher_alerted_at = self._publisher_alerted_at, None
            if alerted_at is not None:
                lasted = format_duration(self._clock() - alerted_at)
                sent = await self._send(
                    _KEY_PUBLISHER,
                    f"✅ Публикация снова работает (проблема длилась ~{lasted}).",
                    announce_recovery=True,
                )
                if not sent:
                    self._publisher_alerted_at = alerted_at

    # ------------------------------------------------------------------- FloodWait

    async def _observe_flood(self, stats: PipelineStats) -> None:
        if stats.flood_wait_seconds < self._config.flood_wait_alert_seconds:
            return
        await self._send(
            _KEY_FLOOD,
            "⏳ Telegram ограничил частоту запросов (FloodWait): пауза "
            f"{format_duration(timedelta(seconds=stats.flood_wait_seconds))}.\n"
            "Чтение и публикация приостановлены, после паузы продолжу сам.",
        )

    # --------------------------------------------------------------------- доставка

    async def _send(self, key: str, text: str, *, announce_recovery: bool = False) -> bool:
        """Логирует и доставляет оповещение. True — считать его отправленным.

        Cooldown действует только на оповещения о проблеме и отсчитывается от
        предыдущего такого оповещения по тому же ключу. Сообщения о восстановлении
        он не ограничивает и не продлевает: проблема, вернувшаяся вскоре после
        починки, именно тот случай, о котором надо сообщить.
        """
        now = self._clock()
        last = self._last_sent.get(key)
        if not announce_recovery and last is not None and now - last < self._cooldown:
            logger.info("Оповещение %s подавлено cooldown: %s", key, text.splitlines()[0])
            return False

        text = redact_secrets(text)
        logger.warning("ОПОВЕЩЕНИЕ [%s]: %s", key, text.replace("\n", " | "))

        if self._sink is not None:
            try:
                await self._sink.send(text)
            except Exception:
                # Доставка не удалась (Telegram недоступен, бот ещё не стартовал):
                # не считаем отправленным — попробуем снова на следующем прогоне.
                logger.exception("Не удалось доставить оповещение %s", key)
                return False

        if not announce_recovery:
            self._last_sent[key] = now
        return True
