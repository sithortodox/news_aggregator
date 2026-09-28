"""Обслуживание SQLite-базы состояния: бэкап с ротацией и VACUUM.

Запускается отдельно от основного процесса (cron на хосте VPS):

    docker compose exec -T news-aggregator python -m news_aggregator.maintenance backup
    docker compose exec -T news-aggregator python -m news_aggregator.maintenance vacuum

Бэкап делается через `VACUUM INTO` — это консистентная копия «на лету»
(безопасна при включённом WAL и работающем приложении, останавливать
контейнер не нужно), причём сразу сжатая, без свободных страниц. Копия
пишется во временный файл и только после успешной проверки целостности
переименовывается в итоговое имя — незавершённый бэкап никогда не
выглядит как готовый.

Бэкап на том же диске защищает от порчи БД, но не от потери VPS. Вынос
копий за пределы сервера (rclone/rsync/scp) — на стороне cron, см.
deploy/backup.sh и раздел README про резервное копирование.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("news_aggregator.maintenance")

DEFAULT_KEEP = 7
_BUSY_TIMEOUT_SECONDS = 60.0


class MaintenanceError(Exception):
    """Ошибка операции обслуживания БД (бэкап не создан, БД повреждена и т.п.)."""


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise MaintenanceError(f"Файл БД не найден: {db_path}")
    return sqlite3.connect(str(db_path), timeout=_BUSY_TIMEOUT_SECONDS)


def _check_integrity(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    finally:
        conn.close()
    if rows != [("ok",)]:
        raise MaintenanceError(f"integrity_check не пройден для {db_path}: {rows[:3]}")


def backup_database(
    db_path: Path, backup_dir: Path, *, keep: int = DEFAULT_KEEP, now: datetime | None = None
) -> Path:
    """Создаёт сжатую копию БД в backup_dir и оставляет последние `keep` копий.

    Returns:
        Путь к созданному файлу бэкапа.
    """
    if keep < 1:
        raise ValueError("keep должен быть >= 1")

    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    final_path = backup_dir / f"{db_path.stem}-{timestamp}.db"
    tmp_path = final_path.with_name(final_path.name + ".tmp")
    if final_path.exists():
        raise MaintenanceError(f"Бэкап уже существует: {final_path}")
    tmp_path.unlink(missing_ok=True)

    conn = _connect(db_path)
    try:
        # Путь подставляется как параметр, а не через f-строку в SQL.
        conn.execute("VACUUM INTO ?", (str(tmp_path),))
    except sqlite3.Error as exc:
        tmp_path.unlink(missing_ok=True)
        raise MaintenanceError(f"Не удалось создать бэкап: {exc}") from exc
    finally:
        conn.close()

    try:
        _check_integrity(tmp_path)
    except MaintenanceError:
        tmp_path.unlink(missing_ok=True)
        raise

    os.replace(tmp_path, final_path)
    removed = rotate_backups(backup_dir, db_path.stem, keep=keep)
    logger.info(
        "Бэкап создан: %s (%d КБ), удалено старых копий: %d",
        final_path,
        final_path.stat().st_size // 1024,
        len(removed),
    )
    return final_path


def rotate_backups(backup_dir: Path, prefix: str, *, keep: int = DEFAULT_KEEP) -> list[Path]:
    """Удаляет все копии `<prefix>-*.db`, кроме `keep` самых новых.

    Имена содержат UTC-метку времени, поэтому лексикографический порядок
    совпадает с хронологическим. Временные `.tmp` файлы не затрагиваются.
    """
    if keep < 1:
        raise ValueError("keep должен быть >= 1")
    backups = sorted(backup_dir.glob(f"{prefix}-*.db"))
    to_remove = backups[:-keep] if len(backups) > keep else []
    for path in to_remove:
        path.unlink()
    return to_remove


def vacuum_database(db_path: Path) -> tuple[int, int]:
    """Сжимает БД на месте: возвращает свободные страницы после DELETE.

    Требует свободного места примерно на размер БД и кратковременно
    блокирует запись — запускать в спокойное время (например, раз в
    неделю ночью). Возвращает (размер до, размер после) в байтах.
    """
    before = db_path.stat().st_size if db_path.is_file() else 0
    conn = _connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
    except sqlite3.Error as exc:
        raise MaintenanceError(f"VACUUM не выполнен: {exc}") from exc
    finally:
        conn.close()
    after = db_path.stat().st_size
    logger.info("VACUUM выполнен: %d КБ -> %d КБ", before // 1024, after // 1024)
    return before, after


def _resolve_db_path(args: argparse.Namespace) -> Path:
    if args.db:
        return Path(args.db)
    # Ленивый импорт: модуль обслуживания не должен тянуть весь стек приложения
    # ради простого запуска с явным --db.
    from news_aggregator.config.loader import ConfigError, load_app_config

    try:
        config = load_app_config(args.config)
    except ConfigError as exc:
        raise MaintenanceError(f"Ошибка конфигурации: {exc}") from exc
    db_path = config.storage.params.get("db_path")
    return Path(db_path) if isinstance(db_path, str) else Path("data/state.db")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="news_aggregator.maintenance",
        description="Обслуживание SQLite-базы состояния агрегатора",
    )
    parser.add_argument("--config", default="config/config.yaml", help="Путь к config.yaml")
    parser.add_argument("--db", default=None, help="Путь к БД (по умолчанию — из config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup", help="Создать сжатую копию БД с ротацией")
    backup.add_argument(
        "--dir", default=None, help="Каталог бэкапов (по умолчанию <каталог БД>/backups)"
    )
    backup.add_argument(
        "--keep", type=int, default=DEFAULT_KEEP, help="Сколько последних копий хранить"
    )

    sub.add_parser("vacuum", help="Сжать БД на месте (вернуть место после удалений)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    args = _parse_args(argv)
    try:
        db_path = _resolve_db_path(args)
        if args.command == "backup":
            backup_dir = Path(args.dir) if args.dir else db_path.parent / "backups"
            path = backup_database(db_path, backup_dir, keep=args.keep)
            print(path)
        else:
            vacuum_database(db_path)
    except (MaintenanceError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
