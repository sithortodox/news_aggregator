from __future__ import annotations

from pathlib import Path

import pytest

from news_aggregator.dedup_explain import explain, main

_PROJECT_CONFIG = str(Path(__file__).resolve().parents[2] / "config" / "config.yaml")

HOCKEY_A = "Сборная России по хоккею обыграла Канаду со счётом 3:1"
HOCKEY_B = "Россияне победили канадцев 3:1 в финале хоккейного турнира в Санкт-Петербурге"
UNRELATED = "Учёные обнаружили новый вид бабочек в тропических лесах Амазонии"


async def test_related_word_forms_are_reported_as_paraphrase_duplicate() -> None:
    report, is_duplicate = await explain(HOCKEY_A, HOCKEY_B, _PROJECT_CONFIG)

    assert is_duplicate is True
    assert "paraphrase_deduplicator" in report
    assert "ИТОГ: дубль (первым сработал paraphrase_deduplicator)" in report
    assert "общие основы" in report
    assert "канад" in report


async def test_unrelated_texts_are_not_duplicates() -> None:
    report, is_duplicate = await explain(HOCKEY_A, UNRELATED, _PROJECT_CONFIG)

    assert is_duplicate is False
    assert "ИТОГ: не дубль" in report


async def test_identical_text_is_caught_by_the_first_cheap_layer() -> None:
    report, is_duplicate = await explain(HOCKEY_A, HOCKEY_A, _PROJECT_CONFIG)

    assert is_duplicate is True
    assert "первым сработал text_hash_deduplicator" in report


async def test_every_layer_is_reported_not_only_the_first_hit() -> None:
    report, _ = await explain(HOCKEY_A, HOCKEY_A, _PROJECT_CONFIG)

    for layer in (
        "text_hash_deduplicator",
        "link_deduplicator",
        "simhash_deduplicator",
        "paraphrase_deduplicator",
    ):
        assert layer in report, layer


async def test_link_variants_are_matched_by_link_layer() -> None:
    first = "Подробности по ставке: https://example.com/news/123"
    second = (
        "Совсем другая формулировка, та же статья https://www.example.com/news/123/?utm_source=tg"
    )

    report, is_duplicate = await explain(first, second, _PROJECT_CONFIG)

    assert is_duplicate is True
    assert "link_deduplicator" in report


async def test_channel_signature_link_is_not_a_duplicate_marker() -> None:
    first = "Первая новость про хоккей и Канаду. Подписаться: https://t.me/mychannel"
    second = "Совсем иная новость про бабочек Амазонии. Подписаться: https://t.me/mychannel"

    _, is_duplicate = await explain(first, second, _PROJECT_CONFIG)

    assert is_duplicate is False


async def test_explain_does_not_create_a_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    await explain(HOCKEY_A, HOCKEY_B, _PROJECT_CONFIG)

    assert list(tmp_path.iterdir()) == []


def test_cli_prints_report_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([HOCKEY_A, HOCKEY_B, "--config", _PROJECT_CONFIG])

    assert exit_code == 0
    assert "ИТОГ:" in capsys.readouterr().out


def test_cli_reports_config_error(tmp_path: Path) -> None:
    bad = tmp_path / "config.yaml"
    bad.write_text("sources: [no: valid: yaml", encoding="utf-8")

    assert main(["a", "b", "--config", str(bad)]) == 1
