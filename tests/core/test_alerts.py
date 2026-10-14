from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from news_aggregator.alerts import AlertManager, format_duration, runs_phrase
from news_aggregator.config.schema import AlertsConfig
from news_aggregator.core.interfaces import IAlertSink
from news_aggregator.core.models import PipelineStats

TOKEN = "8979586318:AAG0fDoGTZ0xtH4gAd64zCpMwhepJTitcy4"


class _Sink(IAlertSink):
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.failing = False

    async def send(self, text: str) -> None:
        if self.failing:
            raise RuntimeError("telegram недоступен")
        self.sent.append(text)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def _stats(
    *,
    failed: tuple[str, ...] = (),
    ok: tuple[str, ...] = (),
    errors: dict[str, str] | None = None,
    publish_failures: int = 0,
    publish_error: str | None = None,
    published: int = 0,
    flood: float = 0.0,
) -> PipelineStats:
    return PipelineStats(
        failed_sources=list(failed),
        succeeded_sources=list(ok),
        source_errors=errors or {},
        publish_failures=publish_failures,
        publish_error=publish_error,
        messages_published=published,
        flood_wait_seconds=flood,
    )


def _make(
    sink: _Sink | None, clock: _Clock | None = None, **config: object
) -> tuple[AlertManager, _Clock]:
    clock = clock or _Clock()
    return AlertManager(AlertsConfig(**config), sink, clock=clock), clock  # type: ignore[arg-type]


async def _fail(manager: AlertManager, source: str, times: int, error: str = "boom") -> None:
    for _ in range(times):
        await manager.observe(_stats(failed=(source,), errors={source: error}))


# ------------------------------------------------------------------- источники


async def test_no_alert_below_threshold() -> None:
    sink = _Sink()
    manager, _ = _make(sink)

    await _fail(manager, "a", 4)

    assert sink.sent == []


async def test_alert_exactly_at_threshold_with_error_text() -> None:
    sink = _Sink()
    manager, _ = _make(sink)

    await _fail(manager, "a", 5, error="ChannelPrivateError: канал закрыт")

    assert len(sink.sent) == 1
    assert "«a»" in sink.sent[0]
    assert "5 прогонов" in sink.sent[0]
    assert "ChannelPrivateError: канал закрыт" in sink.sent[0]


async def test_custom_threshold_is_respected() -> None:
    sink = _Sink()
    manager, _ = _make(sink, source_failure_threshold=2)

    await _fail(manager, "a", 2)

    assert len(sink.sent) == 1


async def test_alert_is_sent_once_per_episode_not_every_run() -> None:
    sink = _Sink()
    manager, _ = _make(sink)

    await _fail(manager, "a", 50)

    assert len(sink.sent) == 1


async def test_long_outage_does_not_repeat_alert_even_after_cooldown_expires() -> None:
    """Эпизод один — оповещение одно, сколько бы часов проблема ни длилась."""
    sink = _Sink()
    manager, clock = _make(sink, cooldown_seconds=3600)
    await _fail(manager, "a", 5)

    for _ in range(5):
        clock.advance(hours=3)
        await _fail(manager, "a", 3)

    assert len(sink.sent) == 1


async def test_a_success_resets_the_consecutive_counter() -> None:
    sink = _Sink()
    manager, _ = _make(sink)

    await _fail(manager, "a", 4)
    await manager.observe(_stats(ok=("a",)))
    await _fail(manager, "a", 4)

    assert sink.sent == []


async def test_sources_not_polled_this_run_are_neutral() -> None:
    """Пауза из-за FloodWait: источника нет ни среди упавших, ни среди успешных."""
    sink = _Sink()
    manager, _ = _make(sink)
    await _fail(manager, "a", 4)

    for _ in range(10):
        await manager.observe(_stats())  # a не опрашивался
    assert sink.sent == []

    await _fail(manager, "a", 1)  # пятый настоящий сбой подряд
    assert len(sink.sent) == 1


async def test_recovery_message_after_alert_with_duration() -> None:
    sink = _Sink()
    manager, clock = _make(sink)
    await _fail(manager, "a", 5)

    clock.advance(minutes=20)
    await manager.observe(_stats(ok=("a",)))

    assert len(sink.sent) == 2
    assert "снова работает" in sink.sent[1]
    assert "20 мин" in sink.sent[1]


async def test_no_recovery_message_if_no_alert_was_sent() -> None:
    sink = _Sink()
    manager, _ = _make(sink)
    await _fail(manager, "a", 3)

    await manager.observe(_stats(ok=("a",)))

    assert sink.sent == []


async def test_recovery_is_sent_only_once() -> None:
    sink = _Sink()
    manager, _ = _make(sink)
    await _fail(manager, "a", 5)

    await manager.observe(_stats(ok=("a",)))
    await manager.observe(_stats(ok=("a",)))

    assert len(sink.sent) == 2


async def test_sources_are_tracked_independently() -> None:
    sink = _Sink()
    manager, _ = _make(sink)

    for _ in range(5):
        await manager.observe(_stats(failed=("bad",), ok=("good",)))

    assert len(sink.sent) == 1
    assert "«bad»" in sink.sent[0]


# ---------------------------------------------------------------------- cooldown


async def test_flapping_source_realerts_only_after_cooldown() -> None:
    sink = _Sink()
    manager, clock = _make(sink, cooldown_seconds=3600)
    await _fail(manager, "a", 5)  # t0: оповещение
    await manager.observe(_stats(ok=("a",)))  # восстановление
    assert len(sink.sent) == 2

    clock.advance(minutes=10)
    await _fail(manager, "a", 5)  # снова упал, но cooldown ещё идёт
    assert len(sink.sent) == 2

    # подавленный эпизод: парного «восстановилось» тоже быть не должно
    await manager.observe(_stats(ok=("a",)))
    assert len(sink.sent) == 2


async def test_suppressed_problem_that_persists_alerts_when_cooldown_expires() -> None:
    sink = _Sink()
    manager, clock = _make(sink, cooldown_seconds=3600)
    await _fail(manager, "a", 5)
    await manager.observe(_stats(ok=("a",)))
    clock.advance(minutes=10)
    await _fail(manager, "a", 5)  # подавлено
    assert len(sink.sent) == 2

    clock.advance(minutes=60)
    await _fail(manager, "a", 1)

    assert len(sink.sent) == 3
    assert "не отвечает" in sink.sent[2]


async def test_problem_returning_long_after_recovery_alerts_again() -> None:
    sink = _Sink()
    manager, clock = _make(sink, cooldown_seconds=3600)
    await _fail(manager, "a", 5)
    clock.advance(minutes=1)
    await manager.observe(_stats(ok=("a",)))

    clock.advance(hours=2)
    await _fail(manager, "a", 5)

    assert len(sink.sent) == 3


async def test_recovery_does_not_extend_the_cooldown() -> None:
    """Проблема через 40 мин после починки (но > cooldown после прошлого
    оповещения о проблеме) должна сообщаться, а не подавляться восстановлением."""
    sink = _Sink()
    manager, clock = _make(sink, cooldown_seconds=3600)
    await _fail(manager, "a", 5)  # t = 0
    clock.advance(minutes=50)
    await manager.observe(_stats(ok=("a",)))  # восстановление на 50-й минуте
    clock.advance(minutes=20)  # 70 мин от первого оповещения, 20 от восстановления

    await _fail(manager, "a", 5)

    assert len(sink.sent) == 3


# ----------------------------------------------------------------------- издатель


async def test_publisher_failure_alerts_on_first_failing_run_by_default() -> None:
    sink = _Sink()
    manager, _ = _make(sink)

    await manager.observe(
        _stats(publish_failures=1, publish_error="TelegramPublisher: ChatWriteForbiddenError")
    )

    assert len(sink.sent) == 1
    assert "публиковать" in sink.sent[0]
    assert "ChatWriteForbiddenError" in sink.sent[0]
    assert "не повторяются" in sink.sent[0]


async def test_long_publisher_outage_does_not_repeat_alert_after_cooldown() -> None:
    sink = _Sink()
    manager, clock = _make(sink, cooldown_seconds=3600)
    await manager.observe(_stats(publish_failures=1))

    for _ in range(5):
        clock.advance(hours=3)
        await manager.observe(_stats(publish_failures=1))

    assert len(sink.sent) == 1


async def test_publisher_threshold_requires_consecutive_failing_runs() -> None:
    sink = _Sink()
    manager, _ = _make(sink, publish_failure_threshold=3)

    await manager.observe(_stats(publish_failures=1))
    await manager.observe(_stats(publish_failures=1))
    assert sink.sent == []

    await manager.observe(_stats(publish_failures=1))
    assert len(sink.sent) == 1


async def test_publisher_alert_once_then_recovery_on_real_publication() -> None:
    sink = _Sink()
    manager, clock = _make(sink)
    for _ in range(5):
        await manager.observe(_stats(publish_failures=1))
    assert len(sink.sent) == 1

    clock.advance(minutes=3)
    await manager.observe(_stats(published=2))

    assert len(sink.sent) == 2
    assert "Публикация снова работает" in sink.sent[1]
    assert "3 мин" in sink.sent[1]


async def test_run_without_posts_does_not_prove_publisher_recovered() -> None:
    sink = _Sink()
    manager, _ = _make(sink)
    await manager.observe(_stats(publish_failures=1))

    for _ in range(5):
        await manager.observe(_stats())  # постов не было

    assert len(sink.sent) == 1  # «восстановилось» не прислали


async def test_partial_success_run_counts_as_failing() -> None:
    sink = _Sink()
    manager, _ = _make(sink)

    await manager.observe(_stats(publish_failures=1, published=3))

    assert len(sink.sent) == 1


# ----------------------------------------------------------------------- FloodWait


async def test_short_floodwait_does_not_alert() -> None:
    sink = _Sink()
    manager, _ = _make(sink, flood_wait_alert_seconds=300)

    await manager.observe(_stats(flood=299))

    assert sink.sent == []


async def test_long_floodwait_alerts_with_duration() -> None:
    sink = _Sink()
    manager, _ = _make(sink, flood_wait_alert_seconds=300)

    await manager.observe(_stats(flood=1800))

    assert len(sink.sent) == 1
    assert "FloodWait" in sink.sent[0]
    assert "30 мин" in sink.sent[0]


async def test_floodwait_exactly_at_threshold_alerts() -> None:
    sink = _Sink()
    manager, _ = _make(sink, flood_wait_alert_seconds=300)

    await manager.observe(_stats(flood=300))

    assert len(sink.sent) == 1


async def test_repeated_floodwaits_are_throttled_by_cooldown() -> None:
    sink = _Sink()
    manager, clock = _make(sink, cooldown_seconds=3600)

    await manager.observe(_stats(flood=600))
    clock.advance(minutes=30)
    await manager.observe(_stats(flood=600))
    assert len(sink.sent) == 1

    clock.advance(minutes=31)
    await manager.observe(_stats(flood=600))
    assert len(sink.sent) == 2


# ------------------------------------------------------------------ доставка/прочее


async def test_disabled_manager_sends_nothing_and_tracks_nothing() -> None:
    sink = _Sink()
    manager, _ = _make(sink, enabled=False)

    await _fail(manager, "a", 10)
    await manager.observe(_stats(publish_failures=1, flood=9999))

    assert sink.sent == []


async def test_without_sink_alerts_are_logged_once_and_not_repeated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager, _ = _make(None)

    with caplog.at_level(logging.WARNING, logger="news_aggregator.alerts"):
        await _fail(manager, "a", 20)

    alerts = [r for r in caplog.records if "ОПОВЕЩЕНИЕ" in r.getMessage()]
    assert len(alerts) == 1
    assert "«a»" in alerts[0].getMessage()


async def test_alert_is_also_logged_when_sink_is_present(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager, _ = _make(_Sink())

    with caplog.at_level(logging.WARNING, logger="news_aggregator.alerts"):
        await manager.observe(_stats(publish_failures=1))

    assert any("ОПОВЕЩЕНИЕ" in r.getMessage() for r in caplog.records)


async def test_failed_delivery_is_not_counted_and_is_retried_next_run() -> None:
    sink = _Sink()
    sink.failing = True
    manager, _ = _make(sink)

    await _fail(manager, "a", 5)  # доставка падает
    assert sink.sent == []

    sink.failing = False
    await _fail(manager, "a", 1)  # следующий прогон — повторяем
    assert len(sink.sent) == 1

    await _fail(manager, "a", 3)  # и больше не повторяем
    assert len(sink.sent) == 1


async def test_failed_delivery_does_not_start_cooldown() -> None:
    sink = _Sink()
    sink.failing = True
    manager, clock = _make(sink, cooldown_seconds=3600)
    await manager.observe(_stats(flood=600))

    sink.failing = False
    clock.advance(seconds=30)
    await manager.observe(_stats(flood=600))

    assert len(sink.sent) == 1


async def test_failed_recovery_delivery_is_retried() -> None:
    sink = _Sink()
    manager, _ = _make(sink)
    await _fail(manager, "a", 5)

    sink.failing = True
    await manager.observe(_stats(ok=("a",)))  # «снова работает» не доставилось
    assert len(sink.sent) == 1

    sink.failing = False
    await manager.observe(_stats(ok=("a",)))
    assert len(sink.sent) == 2
    assert "снова работает" in sink.sent[1]


async def test_failed_publisher_recovery_delivery_is_retried() -> None:
    sink = _Sink()
    manager, _ = _make(sink)
    await manager.observe(_stats(publish_failures=1))

    sink.failing = True
    await manager.observe(_stats(published=1))
    sink.failing = False
    await manager.observe(_stats(published=1))

    assert len(sink.sent) == 2
    assert "Публикация снова работает" in sink.sent[1]


async def test_bot_token_in_error_text_is_masked_in_delivered_alert() -> None:
    sink = _Sink()
    manager, _ = _make(sink)

    await _fail(manager, "a", 5, error=f"ConnectError: https://api.telegram.org/bot{TOKEN}/getMe")

    assert TOKEN not in sink.sent[0]
    assert "<redacted>" in sink.sent[0]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0 с"), (45, "45 с"), (60, "1 мин"), (420, "7 мин"), (3600, "1 ч"), (4800, "1 ч 20 мин")],
)
def test_format_duration(seconds: int, expected: str) -> None:
    assert format_duration(timedelta(seconds=seconds)) == expected


def test_format_duration_clamps_negative_to_zero() -> None:
    assert format_duration(timedelta(seconds=-5)) == "0 с"


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, "1 прогон"),
        (2, "2 прогона"),
        (3, "3 прогона"),
        (4, "4 прогона"),
        (5, "5 прогонов"),
        (11, "11 прогонов"),
        (12, "12 прогонов"),
        (14, "14 прогонов"),
        (21, "21 прогон"),
        (22, "22 прогона"),
        (25, "25 прогонов"),
        (100, "100 прогонов"),
        (101, "101 прогон"),
        (111, "111 прогонов"),
    ],
)
def test_runs_phrase_uses_correct_russian_plural(count: int, expected: str) -> None:
    assert runs_phrase(count) == expected


async def test_alert_text_uses_correct_plural_for_source_and_publisher() -> None:
    sink = _Sink()
    manager, _ = _make(sink, source_failure_threshold=3, publish_failure_threshold=2)

    await _fail(manager, "a", 3)
    await manager.observe(_stats(publish_failures=1))
    await manager.observe(_stats(publish_failures=1))

    assert "3 прогона подряд" in sink.sent[0]
    assert "2 прогона подряд" in sink.sent[1]
