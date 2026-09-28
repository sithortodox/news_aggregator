import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from news_aggregator.maintenance import (
    MaintenanceError,
    backup_database,
    main,
    rotate_backups,
    vacuum_database,
)


def _make_db(path: Path, rows: int = 10) -> Path:
    """Создаёт БД в режиме WAL (как у приложения) с тестовыми данными."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE seen_hashes (text_hash TEXT PRIMARY KEY, payload TEXT)")
    conn.executemany(
        "INSERT INTO seen_hashes VALUES (?, ?)", [(f"h{i}", "x" * 200) for i in range(rows)]
    )
    conn.commit()
    conn.close()
    return path


def _count(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM seen_hashes").fetchone()[0])
    finally:
        conn.close()


def test_backup_creates_consistent_copy(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "state.db", rows=25)

    backup = backup_database(db, tmp_path / "backups")

    assert backup.is_file()
    assert backup.name.startswith("state-") and backup.suffix == ".db"
    assert _count(backup) == 25
    assert not list((tmp_path / "backups").glob("*.tmp"))


def test_backup_works_while_source_connection_is_open(tmp_path: Path) -> None:
    """Приложение держит соединение открытым — бэкап не должен требовать остановки."""
    db = _make_db(tmp_path / "state.db")
    live = sqlite3.connect(str(db))
    live.execute("INSERT INTO seen_hashes VALUES ('live', 'y')")
    live.commit()
    try:
        backup = backup_database(db, tmp_path / "backups")
    finally:
        live.close()

    assert _count(backup) == 11


def test_backup_missing_db_raises(tmp_path: Path) -> None:
    with pytest.raises(MaintenanceError):
        backup_database(tmp_path / "nope.db", tmp_path / "backups")


def test_backup_refuses_to_overwrite_existing(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "state.db")
    moment = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    backup_database(db, tmp_path / "backups", now=moment)

    with pytest.raises(MaintenanceError):
        backup_database(db, tmp_path / "backups", now=moment)


def test_backup_rotation_keeps_only_newest(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "state.db")
    start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    for i in range(5):
        backup_database(db, tmp_path / "backups", keep=3, now=start + timedelta(hours=i))

    names = sorted(p.name for p in (tmp_path / "backups").glob("state-*.db"))

    assert names == [
        "state-20260101-140000.db",
        "state-20260101-150000.db",
        "state-20260101-160000.db",
    ]


def test_rotation_ignores_foreign_files_and_tmp(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "state-20260101-100000.db").write_text("a")
    (backups / "state-20260101-110000.db").write_text("b")
    (backups / "state-20260101-120000.db.tmp").write_text("partial")
    (backups / "notes.txt").write_text("keep me")

    removed = rotate_backups(backups, "state", keep=1)

    assert [p.name for p in removed] == ["state-20260101-100000.db"]
    assert (backups / "notes.txt").exists()
    assert (backups / "state-20260101-120000.db.tmp").exists()


def test_invalid_keep_raises(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "state.db")
    with pytest.raises(ValueError):
        backup_database(db, tmp_path / "backups", keep=0)
    with pytest.raises(ValueError):
        rotate_backups(tmp_path, "state", keep=0)


def test_vacuum_reclaims_space_after_deletes(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "state.db", rows=2000)
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM seen_hashes WHERE rowid % 10 != 0")
    conn.commit()
    conn.close()

    before, after = vacuum_database(db)

    assert after < before
    assert _count(db) == 200


def test_cli_backup_and_vacuum(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = _make_db(tmp_path / "state.db")
    backups = tmp_path / "b"

    assert main(["--db", str(db), "backup", "--dir", str(backups), "--keep", "2"]) == 0
    printed = capsys.readouterr().out.strip()
    assert Path(printed).is_file()

    assert main(["--db", str(db), "vacuum"]) == 0


def test_cli_returns_error_code_for_missing_db(tmp_path: Path) -> None:
    assert main(["--db", str(tmp_path / "nope.db"), "backup"]) == 1


def test_cli_default_backup_dir_is_next_to_db(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "state.db")

    assert main(["--db", str(db), "backup"]) == 0

    assert len(list((tmp_path / "backups").glob("state-*.db"))) == 1


def _touch_backup(directory: Path, moment: datetime, prefix: str = "state") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{prefix}-{moment.strftime('%Y%m%d-%H%M%S')}.db"
    path.write_text("x")
    return path


def test_rotation_removes_backups_older_than_max_age(tmp_path: Path) -> None:
    now = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
    backups = tmp_path / "backups"
    old = _touch_backup(backups, now - timedelta(hours=30))
    borderline_ok = _touch_backup(backups, now - timedelta(hours=23, minutes=59))
    fresh = _touch_backup(backups, now - timedelta(hours=1))

    removed = rotate_backups(backups, "state", keep=10, max_age_hours=24, now=now)

    assert removed == [old]
    assert borderline_ok.exists() and fresh.exists()


def test_rotation_keeps_backup_exactly_at_max_age(tmp_path: Path) -> None:
    """Возраст ровно 24 ч — ещё не «старше 24 ч»."""
    now = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
    backups = tmp_path / "backups"
    exact = _touch_backup(backups, now - timedelta(hours=24))
    _touch_backup(backups, now - timedelta(hours=1))

    assert rotate_backups(backups, "state", keep=10, max_age_hours=24, now=now) == []
    assert exact.exists()


def test_rotation_never_removes_newest_even_if_too_old(tmp_path: Path) -> None:
    """Cron не работал несколько дней: единственная копия нужна, хоть и старая."""
    now = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
    backups = tmp_path / "backups"
    stale_1 = _touch_backup(backups, now - timedelta(days=5))
    stale_2 = _touch_backup(backups, now - timedelta(days=3))

    removed = rotate_backups(backups, "state", keep=10, max_age_hours=24, now=now)

    assert removed == [stale_1]
    assert stale_2.exists()


def test_rotation_by_age_ignores_files_with_unparseable_names(tmp_path: Path) -> None:
    now = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
    backups = tmp_path / "backups"
    _touch_backup(backups, now - timedelta(days=9))
    _touch_backup(backups, now - timedelta(hours=1))
    foreign = backups / "state-manual-copy.db"
    foreign.write_text("mine")

    rotate_backups(backups, "state", keep=10, max_age_hours=24, now=now)

    assert foreign.exists()


def test_rotation_on_empty_or_missing_dir_returns_nothing(tmp_path: Path) -> None:
    assert rotate_backups(tmp_path / "missing", "state", max_age_hours=24) == []


def test_invalid_max_age_raises(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "state.db")
    with pytest.raises(ValueError):
        backup_database(db, tmp_path / "backups", max_age_hours=0)
    with pytest.raises(ValueError):
        rotate_backups(tmp_path, "state", max_age_hours=-1)


def test_backup_database_applies_age_rotation(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "state.db")
    start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    for hours in (0, 10, 30):
        backup_database(
            db, tmp_path / "backups", keep=10, max_age_hours=24, now=start + timedelta(hours=hours)
        )

    names = sorted(p.name for p in (tmp_path / "backups").glob("state-*.db"))

    # к моменту третьего бэкапа (+30 ч) копия «+0 ч» старше суток, «+10 ч» — нет
    assert names == ["state-20260101-100000.db", "state-20260102-060000.db"]


def test_cli_default_max_age_is_24_hours(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "state.db")
    backups = tmp_path / "backups"
    ancient = _touch_backup(backups, datetime(2020, 1, 1, tzinfo=UTC))

    assert main(["--db", str(db), "backup"]) == 0

    assert not ancient.exists()
    assert len(list(backups.glob("state-*.db"))) == 1


def test_cli_max_age_zero_disables_age_rotation(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "state.db")
    backups = tmp_path / "backups"
    ancient = _touch_backup(backups, datetime(2020, 1, 1, tzinfo=UTC))

    assert main(["--db", str(db), "backup", "--max-age-hours", "0"]) == 0

    assert ancient.exists()
