"""Остановка по сигналу и сторож зависаний основного цикла (main._run_forever).

Сигналы шлются настоящие (os.kill себе): проверяется именно то, что делает
`docker stop`.
"""

from __future__ import annotations

import asyncio
import os
import signal

import pytest

from news_aggregator.core.models import PipelineStats
from news_aggregator.main import RunStalledError, _run_forever


class _SignalPipeline:
    """Шлёт себе сигнал посреди прогона; после request_stop может зависнуть."""

    def __init__(
        self,
        *,
        signal_on_run: int | None = None,
        signals: int = 1,
        hang_after_stop: bool = False,
    ) -> None:
        self.calls = 0
        self.stop_requests = 0
        self.finished_runs = 0
        self._signal_on_run = signal_on_run
        self._signals = signals
        self._hang_after_stop = hang_after_stop

    def request_stop(self) -> None:
        self.stop_requests += 1

    async def run_once(self) -> PipelineStats:
        self.calls += 1
        if self.calls == self._signal_on_run:
            for _ in range(self._signals):
                os.kill(os.getpid(), signal.SIGTERM)
                # Обработчик сигнала выполняется на следующей итерации event loop.
                for _ in range(5):
                    await asyncio.sleep(0)
            if self._hang_after_stop:
                await asyncio.sleep(3600)
        self.finished_runs += 1
        return PipelineStats()


async def test_sigterm_mid_run_finishes_the_run_and_exits() -> None:
    pipeline = _SignalPipeline(signal_on_run=2)

    await asyncio.wait_for(_run_forever(pipeline, 0, None, None), timeout=5)  # type: ignore[arg-type]

    assert pipeline.stop_requests == 1
    assert pipeline.calls == 2
    assert pipeline.finished_runs == 2  # прогон не оборван


async def test_sigterm_during_sleep_exits_without_waiting_for_interval() -> None:
    pipeline = _SignalPipeline()

    async def send_later() -> None:
        await asyncio.sleep(0.05)
        os.kill(os.getpid(), signal.SIGTERM)

    sender = asyncio.create_task(send_later())
    await asyncio.wait_for(_run_forever(pipeline, 3600, None, None), timeout=5)  # type: ignore[arg-type]
    await sender

    assert pipeline.calls == 1


async def test_second_signal_stops_immediately() -> None:
    pipeline = _SignalPipeline(signal_on_run=1, signals=2, hang_after_stop=True)

    await asyncio.wait_for(_run_forever(pipeline, 0, None, None), timeout=5)  # type: ignore[arg-type]

    assert pipeline.finished_runs == 0


async def test_signal_handlers_are_removed_after_exit() -> None:
    pipeline = _SignalPipeline(signal_on_run=1)

    await asyncio.wait_for(_run_forever(pipeline, 0, None, None), timeout=5)  # type: ignore[arg-type]

    assert signal.getsignal(signal.SIGTERM) in (signal.SIG_DFL, None)


# ------------------------------------------------------------------ сторож зависаний


class _WatchedPipeline:
    def __init__(self, *, hang: bool, idle_seconds: float) -> None:
        self.calls = 0
        self.cancelled = False
        self._hang = hang
        self._idle = idle_seconds

    def request_stop(self) -> None:
        pass

    def seconds_since_progress(self) -> float:
        return self._idle

    async def run_once(self) -> PipelineStats:
        self.calls += 1
        if self._hang:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        await asyncio.sleep(0.1)
        if self.calls == 3:
            raise asyncio.CancelledError
        return PipelineStats()


class _RecordingAlerts:
    def __init__(self) -> None:
        self.notified: list[tuple[str, str]] = []

    async def observe(self, stats: object) -> None:
        pass

    async def notify(self, key: str, text: str) -> None:
        self.notified.append((key, text))


async def test_stalled_run_is_cancelled_and_reported() -> None:
    pipeline = _WatchedPipeline(hang=True, idle_seconds=400)
    alerts = _RecordingAlerts()

    with pytest.raises(RunStalledError):
        await asyncio.wait_for(
            _run_forever(
                pipeline,  # type: ignore[arg-type]
                0,
                None,
                None,
                None,
                alerts,  # type: ignore[arg-type]
                stall_timeout_seconds=1,
            ),
            timeout=5,
        )

    assert pipeline.cancelled
    assert [key for key, _ in alerts.notified] == ["stalled"]


async def test_long_run_with_progress_is_not_treated_as_stall() -> None:
    # Прогон идёт дольше порога, но прогресс свежий: сторож молчит.
    pipeline = _WatchedPipeline(hang=False, idle_seconds=0.0)

    await asyncio.wait_for(
        _run_forever(pipeline, 0, None, None, stall_timeout_seconds=0.05),  # type: ignore[arg-type]
        timeout=5,
    )

    assert pipeline.calls == 3
