"""Heartbeat-файл и Docker HEALTHCHECK для процесса-демона агрегатора.

Основной цикл (``main._run_forever``) вызывает write_heartbeat() сразу
после каждого успешного ``pipeline.run_once()``. Эта же проверка
используется Docker'ом (см. ``HEALTHCHECK`` в Dockerfile) и может
использоваться внешним watchdog/мониторингом: если метка времени в файле
"протухла" сильнее, чем poll_interval_seconds + запас, значит основной цикл
завис, упал или заблокирован (например, на медленном сетевом вызове без
таймаута) — и контейнер стоит считать нездоровым.

Важный нюанс Docker: HEALTHCHECK сам по себе только помечает контейнер
``unhealthy`` (видно в ``docker compose ps``/``docker inspect``) — это НЕ
приводит к автоматическому перезапуску, ``restart: unless-stopped``
реагирует на завершение процесса, а не на статус healthcheck. Для
автоматического перезапуска по unhealthy нужен отдельный watchdog (например,
контейнер ``willfarrell/autoheal``) — само по себе это уже даёт видимость
проблемы в стандартных Docker-инструментах и внешних мониторингах.

Запуск вручную:
    python -m news_aggregator.healthcheck [--config config/config.yaml] \
        [--file PATH] [--margin-seconds N]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from news_aggregator.config.schema import AppConfig

logger = logging.getLogger("news_aggregator.healthcheck")

# Дополнительный запас поверх poll_interval_seconds: сам прогон тоже
# занимает время (сеть, FloodWait), а не только пауза между прогонами.
DEFAULT_MARGIN_SECONDS = 60.0


def heartbeat_path_for_db(db_path: Path) -> Path:
    """Путь к heartbeat-файлу — рядом с БД состояния, в том же volume."""
    return db_path.parent / "heartbeat"


def write_heartbeat(path: Path, now: datetime | None = None) -> None:
    """Атомарно записывает текущую метку времени (UTC, ISO8601) в heartbeat-файл.

    Пишет во временный файл и переименовывает — сторонний процесс (Docker,
    watchdog) никогда не увидит частично записанный файл.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text((now or datetime.now(UTC)).isoformat())
    os.replace(tmp_path, path)


def read_heartbeat(path: Path) -> datetime | None:
    """Читает метку времени из heartbeat-файла. None, если файла нет или он битый."""
    try:
        text = path.read_text().strip()
    except FileNotFoundError:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def check_heartbeat(
    path: Path, max_age_seconds: float, *, now: datetime | None = None
) -> tuple[bool, str]:
    """Проверяет свежесть heartbeat-файла.

    Returns:
        (healthy, сообщение) — сообщение годится и для лога, и для вывода CLI.
    """
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds должен быть положительным")

    moment = now or datetime.now(UTC)
    heartbeat = read_heartbeat(path)
    if heartbeat is None:
        return False, f"heartbeat-файл {path} отсутствует или повреждён"

    age = (moment - heartbeat).total_seconds()
    if age > max_age_seconds:
        return False, f"heartbeat устарел: {age:.0f}с назад (порог {max_age_seconds:.0f}с)"
    if age < -max_age_seconds:
        # Часы контейнера/файловой системы разъехались с системным временем
        # больше чем на порог — не блокируем на этом (это не "зависание"),
        # но явно сообщаем, потому что это тоже симптом проблемы окружения.
        return True, f"heartbeat из будущего (на {-age:.0f}с) — проверьте часы контейнера"
    return True, f"heartbeat свежий: {age:.0f}с назад"


def _db_path_from_config(config: AppConfig) -> Path:
    db_path = config.storage.params.get("db_path") if config.storage.name == "sqlite" else None
    return Path(db_path) if isinstance(db_path, str) else Path("data/state.db")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="news_aggregator.healthcheck",
        description="Проверка свежести heartbeat-файла основного цикла агрегатора",
    )
    parser.add_argument("--config", default="config/config.yaml", help="Путь к config.yaml")
    parser.add_argument(
        "--file", default=None, help="Явный путь к heartbeat-файлу (по умолчанию — рядом с БД)"
    )
    parser.add_argument(
        "--margin-seconds",
        type=float,
        default=DEFAULT_MARGIN_SECONDS,
        help=f"Запас поверх poll_interval_seconds (по умолчанию {DEFAULT_MARGIN_SECONDS:.0f})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)

    from news_aggregator.config.loader import ConfigError, load_app_config

    try:
        config = load_app_config(args.config)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 1

    path = Path(args.file) if args.file else heartbeat_path_for_db(_db_path_from_config(config))
    max_age = config.pipeline.poll_interval_seconds + args.margin_seconds

    healthy, message = check_heartbeat(path, max_age)
    print(message)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
