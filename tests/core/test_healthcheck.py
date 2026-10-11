import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from news_aggregator.healthcheck import (
    check_heartbeat,
    heartbeat_path_for_db,
    main,
    read_heartbeat,
    write_heartbeat,
)
from news_aggregator.main import _run_forever


def test_heartbeat_path_is_next_to_db() -> None:
    assert heartbeat_path_for_db(Path("data/state.db")) == Path("data/heartbeat")


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    moment = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    write_heartbeat(path, now=moment)

    assert read_heartbeat(path) == moment


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "heartbeat"

    write_heartbeat(path)

    assert path.is_file()


def test_write_leaves_no_tmp_file_behind(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"

    write_heartbeat(path)

    assert not path.with_name("heartbeat.tmp").exists()


def test_read_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_heartbeat(tmp_path / "missing") is None


def test_read_corrupted_file_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    path.write_text("это не дата")

    assert read_heartbeat(path) is None


def test_check_heartbeat_fresh_is_healthy(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    write_heartbeat(path, now=now - timedelta(seconds=30))

    healthy, message = check_heartbeat(path, max_age_seconds=60, now=now)

    assert healthy is True
    assert "свежий" in message


def test_check_heartbeat_stale_is_unhealthy(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    write_heartbeat(path, now=now - timedelta(seconds=120))

    healthy, message = check_heartbeat(path, max_age_seconds=60, now=now)

    assert healthy is False
    assert "устарел" in message


def test_check_heartbeat_exactly_at_boundary_is_healthy(tmp_path: Path) -> None:
    """age == max_age_seconds ещё не 'устарел' (строгое >)."""
    path = tmp_path / "heartbeat"
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    write_heartbeat(path, now=now - timedelta(seconds=60))

    healthy, _ = check_heartbeat(path, max_age_seconds=60, now=now)

    assert healthy is True


def test_check_heartbeat_missing_file_is_unhealthy(tmp_path: Path) -> None:
    healthy, message = check_heartbeat(tmp_path / "missing", max_age_seconds=60)

    assert healthy is False
    assert "отсутствует" in message


def test_check_heartbeat_from_future_is_healthy_but_flagged(tmp_path: Path) -> None:
    """Часы разъехались сильнее порога - не блокируем, но сообщаем."""
    path = tmp_path / "heartbeat"
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    write_heartbeat(path, now=now + timedelta(seconds=120))

    healthy, message = check_heartbeat(path, max_age_seconds=60, now=now)

    assert healthy is True
    assert "будущего" in message


def test_check_heartbeat_slightly_ahead_is_healthy_and_not_flagged(tmp_path: Path) -> None:
    """Небольшое рассогласование часов (в пределах порога) - штатная ситуация."""
    path = tmp_path / "heartbeat"
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    write_heartbeat(path, now=now + timedelta(seconds=5))

    healthy, message = check_heartbeat(path, max_age_seconds=60, now=now)

    assert healthy is True
    assert "будущего" not in message


def test_check_heartbeat_rejects_non_positive_max_age(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        check_heartbeat(tmp_path / "heartbeat", max_age_seconds=0)


def _config(tmp_path: Path, poll_interval: int = 60) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
pipeline:
  poll_interval_seconds: {poll_interval}
sources: []
filters: []
deduplicators: []
enrichers: []
publishers:
  - name: console_publisher
storage:
  name: sqlite
  params:
    db_path: "{tmp_path / "state.db"}"
""",
        encoding="utf-8",
    )
    return config_path


def test_cli_healthy_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = _config(tmp_path, poll_interval=60)
    write_heartbeat(tmp_path / "heartbeat")

    exit_code = main(["--config", str(config_path)])

    assert exit_code == 0
    assert "свежий" in capsys.readouterr().out


def test_cli_unhealthy_when_heartbeat_missing(tmp_path: Path) -> None:
    config_path = _config(tmp_path, poll_interval=60)

    assert main(["--config", str(config_path)]) == 1


def test_cli_unhealthy_when_heartbeat_older_than_poll_interval_plus_margin(
    tmp_path: Path,
) -> None:
    config_path = _config(tmp_path, poll_interval=10)
    write_heartbeat(tmp_path / "heartbeat", now=datetime.now(UTC) - timedelta(seconds=10 + 60 + 5))

    assert main(["--config", str(config_path), "--margin-seconds", "60"]) == 1


def test_cli_respects_explicit_file_override(tmp_path: Path) -> None:
    config_path = _config(tmp_path)
    custom = tmp_path / "custom-heartbeat"
    write_heartbeat(custom)

    assert main(["--config", str(config_path), "--file", str(custom)]) == 0


def test_cli_reports_config_error(tmp_path: Path) -> None:
    bad_config = tmp_path / "config.yaml"
    bad_config.write_text("sources: [this is not: valid: yaml: at all", encoding="utf-8")

    assert main(["--config", str(bad_config)]) == 1


class _FakePipeline:
    """Подмена AggregatorPipeline: считает вызовы и может падать/останавливать цикл."""

    def __init__(
        self,
        *,
        stop_after: int | None = None,
        fail_on: int | None = None,
        heartbeat_path: Path | None = None,
    ) -> None:
        self.calls = 0
        self._stop_after = stop_after
        self._fail_on = fail_on
        self._heartbeat_path = heartbeat_path
        self.heartbeat_existed_during_first_run: bool | None = None

    async def run_once(self) -> None:
        self.calls += 1
        if self.calls == 1 and self._heartbeat_path is not None:
            self.heartbeat_existed_during_first_run = self._heartbeat_path.exists()
        if self._fail_on == self.calls:
            raise RuntimeError("прогон упал")
        if self._stop_after == self.calls:
            raise asyncio.CancelledError


async def test_run_forever_writes_heartbeat_after_successful_run(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat"
    pipeline = _FakePipeline(stop_after=3)

    await _run_forever(pipeline, 0, None, path)  # type: ignore[arg-type]

    assert pipeline.calls == 3
    assert read_heartbeat(path) is not None


async def test_run_forever_does_not_pre_write_heartbeat_before_first_run(
    tmp_path: Path,
) -> None:
    """Heartbeat должен означать 'цикл продвигается', а не 'процесс стартовал'."""
    path = tmp_path / "heartbeat"
    pipeline = _FakePipeline(stop_after=2, heartbeat_path=path)

    await _run_forever(pipeline, 0, None, path)  # type: ignore[arg-type]

    assert pipeline.heartbeat_existed_during_first_run is False


async def test_run_forever_does_not_write_heartbeat_if_first_run_crashes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "heartbeat"
    pipeline = _FakePipeline(fail_on=1)

    with pytest.raises(RuntimeError):
        await _run_forever(pipeline, 0, None, path)  # type: ignore[arg-type]

    assert not path.exists()


async def test_run_forever_keeps_last_good_heartbeat_when_later_run_crashes(
    tmp_path: Path,
) -> None:
    """После падения heartbeat не обновляется — он должен 'протухнуть' и
    healthcheck заметит проблему."""
    path = tmp_path / "heartbeat"
    pipeline = _FakePipeline(fail_on=3)

    with pytest.raises(RuntimeError):
        await _run_forever(pipeline, 0, None, path)  # type: ignore[arg-type]

    assert pipeline.calls == 3
    assert read_heartbeat(path) is not None  # от 2-го, успешного прогона


async def test_run_forever_works_without_heartbeat_path() -> None:
    pipeline = _FakePipeline(stop_after=2)

    await _run_forever(pipeline, 0, None, None)  # type: ignore[arg-type]

    assert pipeline.calls == 2
