"""Проверка настоящей модели. Нужны файлы модели: путь в NEWS_AGGREGATOR_MODEL_DIR
(в Docker-образе — /app/models/paraphrase-multilingual-MiniLM-L12-v2)."""

import os
from pathlib import Path

import pytest

from news_aggregator.embedding.onnx_embedder import OnnxTextEmbedder

_MODEL_DIR = os.environ.get("NEWS_AGGREGATOR_MODEL_DIR", "")

needs_model = pytest.mark.skipif(
    not (_MODEL_DIR and Path(_MODEL_DIR, "model_quantized.onnx").exists()),
    reason="нет файлов модели (NEWS_AGGREGATOR_MODEL_DIR)",
)


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_missing_model_files_are_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="model_quantized.onnx"):
        OnnxTextEmbedder(str(tmp_path))


@needs_model
def test_retellings_are_closer_than_unrelated_news() -> None:
    embedder = OnnxTextEmbedder(_MODEL_DIR)
    rain_a = embedder.embed(
        "Сегодня ночью Москву может накрыть сильнейший с 1967 года ливень, прогнозируют "
        "метеорологи."
    )
    rain_b = embedder.embed(
        "Сильнейший с 1967 года ливень обрушится на Москву сегодня — на столицу обрушится "
        "объём воды, равный почти 300 Черкизовским прудам."
    )
    other = embedder.embed("Google выпустила Gemini 4 Hydrogen, новинку Flash-линейки.")

    assert len(rain_a) == 384
    assert _cosine(rain_a, rain_a) == pytest.approx(1.0, abs=1e-5)
    assert _cosine(rain_a, rain_b) > 0.8
    assert _cosine(rain_a, other) < 0.5
