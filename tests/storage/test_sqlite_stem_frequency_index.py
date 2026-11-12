from pathlib import Path

import pytest

from news_aggregator.storage.sqlite_stem_frequency_index import SqliteStemFrequencyIndex


async def test_counts_documents_per_stem_and_survives_restart(tmp_path: Path) -> None:
    db = str(tmp_path / "state.db")
    index = SqliteStemFrequencyIndex(db)
    await index.add(frozenset({"хуанг", "любов"}))
    await index.add(frozenset({"хуанг", "nvidi"}))
    await index.close()

    reopened = SqliteStemFrequencyIndex(db)
    total, counts = await reopened.load()
    await reopened.close()

    assert total == 2
    assert counts == {"хуанг": 2, "любов": 1, "nvidi": 1}


async def test_empty_index(tmp_path: Path) -> None:
    index = SqliteStemFrequencyIndex(str(tmp_path / "state.db"))

    assert await index.load() == (0, {})
    await index.close()


def test_rejects_unsafe_table_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SqliteStemFrequencyIndex(str(tmp_path / "state.db"), table_name="x; DROP TABLE y")
