import logging
import math
from datetime import UTC, datetime, timedelta

import pytest

from news_aggregator.core.interfaces import ITextEmbedder
from news_aggregator.core.models import EmbeddingRecord, ProcessedMessage, RawMessage
from news_aggregator.core.text_utils import compute_text_hash, extract_links, normalize_text
from news_aggregator.dedup.embedding_deduplicator import EmbeddingDeduplicator
from tests.support.in_memory_embedding_index import InMemoryEmbeddingIndex

# Регрессия: my_news_agregator_channel 9358 (naebnet) и 9360 (trendrunet) — один
# ливень, общих слов семь из пятидесяти, лексические слои пару пропустили.
_NAEBNET = (
    "Сегодня ночью Москву может накрыть сильнейший с 1967 года ливень, прогнозируют "
    "метеорологи. С вечера пятницы до утра субботы ожидается до 54% месячной нормы осадков."
)
_TRENDRUNET = (
    "⚡️ Сильнейший с 1967 года ливень обрушится на Москву сегодня — на столицу обрушится "
    "объём воды, равный почти 300 Черкизовским прудам."
)
_DIGEST = "Подборка полезных материалов недели: статьи, видео и инструменты для разработчиков."


class _Embedder(ITextEmbedder):
    """Векторы по ключевому слову: тексты про ливень близки, остальные — нет."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._error = error

    def embed(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        if "ливень" in text:
            angle = 0.0 if "метеорологи" in text else 0.5  # косинус 0.88
        elif "Подборка" in text:
            angle = 0.2  # близко по смыслу, но общих слов нет
        else:
            angle = 1.5
        return (math.cos(angle), math.sin(angle))


def _message(text: str, source_id: str, external_id: str) -> ProcessedMessage:
    normalized = normalize_text(text)
    now = datetime.now(UTC)
    return ProcessedMessage(
        raw=RawMessage(
            source_id=source_id, external_id=external_id, text=text, posted_at=now, fetched_at=now
        ),
        normalized_text=normalized,
        text_hash=compute_text_hash(normalized),
        links=extract_links(text),
    )


def _dedup(
    index: InMemoryEmbeddingIndex, embedder: ITextEmbedder | None, *, dry_run: bool = False
) -> EmbeddingDeduplicator:
    return EmbeddingDeduplicator(index, embedder, min_similarity=0.80, dry_run=dry_run)


async def _published(dedup: EmbeddingDeduplicator, text: str = _NAEBNET) -> None:
    message = _message(text, "naebnet", "17925")
    assert (await dedup.check(message)).is_duplicate is False
    await dedup.remember(message)


async def test_retelling_from_another_channel_is_duplicate() -> None:
    dedup = _dedup(InMemoryEmbeddingIndex(), _Embedder())
    await _published(dedup)

    result = await dedup.check(_message(_TRENDRUNET, "trendrunet", "7605"))

    assert result.is_duplicate is True
    assert "naebnet" in result.reason
    assert "косинус 0.88" in result.reason


async def test_dry_run_only_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    dedup = _dedup(InMemoryEmbeddingIndex(), _Embedder(), dry_run=True)
    await _published(dedup)

    result = await dedup.check(_message(_TRENDRUNET, "trendrunet", "7605"))

    assert result.is_duplicate is False
    assert result.reason.startswith("пробно: ")
    assert "По смыслу (пробно, не отброшено): trendrunet/7605" in caplog.text


async def test_same_channel_is_not_compared() -> None:
    dedup = _dedup(InMemoryEmbeddingIndex(), _Embedder())
    await _published(dedup)

    result = await dedup.check(_message(_TRENDRUNET, "naebnet", "17926"))

    assert result.is_duplicate is False


async def test_similar_genre_without_shared_words_is_not_duplicate() -> None:
    dedup = _dedup(InMemoryEmbeddingIndex(), _Embedder())
    await _published(dedup)  # косинус к подборке 0.98, но общих основ нет

    result = await dedup.check(_message(_DIGEST, "kod_ru", "1"))

    assert result.is_duplicate is False


async def test_unrelated_post_is_not_duplicate() -> None:
    dedup = _dedup(InMemoryEmbeddingIndex(), _Embedder())
    await _published(dedup)

    result = await dedup.check(
        _message("Учёные описали новый вид бабочек в лесах Амазонии и Перу", "nplusone", "1")
    )

    assert result.is_duplicate is False
    assert "ближайший косинус" in result.reason


async def test_short_text_is_skipped() -> None:
    embedder = _Embedder()
    dedup = _dedup(InMemoryEmbeddingIndex(), embedder)

    result = await dedup.check(_message("Ливень!\n\n👍 Бэкдор", "whackdoor", "1"))

    assert result.is_duplicate is False
    assert embedder.calls == []


async def test_vector_is_computed_once_per_message() -> None:
    embedder = _Embedder()
    index = InMemoryEmbeddingIndex()
    dedup = _dedup(index, embedder)
    message = _message(_NAEBNET + "\n\n👍 Бэкдор", "naebnet", "1")

    await dedup.check(message)
    await dedup.remember(message)

    assert len(embedder.calls) == 1
    assert "Бэкдор" not in embedder.calls[0]  # подпись канала не входит в текст
    assert len(index.items) == 1


async def test_without_model_nothing_is_dropped() -> None:
    dedup = _dedup(InMemoryEmbeddingIndex(), None)

    result = await dedup.check(_message(_TRENDRUNET, "trendrunet", "1"))

    assert result.is_duplicate is False
    assert "модель" in result.reason


async def test_model_failure_does_not_block_the_post() -> None:
    dedup = _dedup(InMemoryEmbeddingIndex(), _Embedder(RuntimeError("сбой модели")))

    result = await dedup.check(_message(_TRENDRUNET, "trendrunet", "1"))

    assert result.is_duplicate is False


async def test_old_records_are_purged() -> None:
    index = InMemoryEmbeddingIndex()
    index.items.append(
        (
            EmbeddingRecord(vector=(1.0, 0.0), source_id="old", stems=frozenset()),
            "0",
            datetime.now(UTC) - timedelta(hours=48),
        )
    )
    dedup = _dedup(index, _Embedder())

    await _published(dedup)

    assert [external_id for _, external_id, _ in index.items] == ["17925"]


@pytest.mark.parametrize(
    "kwargs",
    [{"min_similarity": 0.0}, {"min_similarity": 1.5}, {"lookback_hours": 0}, {"stem_length": 1}],
)
def test_params_are_validated(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        EmbeddingDeduplicator(InMemoryEmbeddingIndex(), None, **kwargs)  # type: ignore[arg-type]
