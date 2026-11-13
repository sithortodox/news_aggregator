"""Объяснение дедупликации: почему два текста считаются / не считаются дублями.

    python -m news_aggregator.dedup_explain "текст уже опубликованного поста" "новый текст"

Прогоняет пару через ТЕ ЖЕ дедупликаторы с теми же параметрами из config.yaml,
что и боевой пайплайн (классы настоящие, без копии логики), но с хранилищем в
памяти: первый текст считается опубликованным, второй проверяется. В отличие
от пайплайна, показывает вердикт КАЖДОГО слоя, а не только первого сработавшего,
и подробности сравнения по леммам. Ничего не пишет в БД и не ходит в сеть.

Весовое правило paraphrase_deduplicator (weighted_min_jaccard) работает только
с частотами слов. С --frequencies data/state.db они читаются из боевой базы
(только чтение, база не меняется), без него правило молчит.

Нужен, чтобы разобрать конкретный пропущенный дубль или ложное срабатывание
и подобрать пороги по реальным примерам, а не вслепую.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import UTC, datetime
from typing import cast

from news_aggregator.bootstrap import build_registry
from news_aggregator.config.loader import ConfigError, load_app_config
from news_aggregator.core.interfaces import (
    IDeduplicator,
    IEmbeddingIndex,
    IImageHashIndex,
    ILemmaSetIndex,
    ISimhashIndex,
    IStemFrequencyIndex,
    IStorage,
)
from news_aggregator.core.lemmatize import extract_lemma_set, numbers_in, stem_set
from news_aggregator.core.models import (
    EmbeddingRecord,
    ImageHashRecord,
    ProcessedMessage,
    RawMessage,
)
from news_aggregator.core.text_utils import compute_text_hash, extract_links, normalize_text


class _MemoryStorage(IStorage):
    def __init__(self) -> None:
        self._hashes: set[str] = set()
        self._links: dict[str, str] = {}

    async def get_last_external_id(self, source_id: str) -> str | None:
        return None

    async def set_last_external_id(self, source_id: str, external_id: str) -> None:
        return None

    async def has_hash(self, text_hash: str) -> bool:
        return text_hash in self._hashes

    async def save_hash(self, text_hash: str, source_id: str, external_id: str) -> None:
        self._hashes.add(text_hash)

    async def has_link(self, link: str) -> bool:
        return link in self._links

    async def get_link_source(self, link: str) -> str | None:
        return self._links.get(link)

    async def save_link(self, link: str, source_id: str, external_id: str) -> None:
        self._links.setdefault(link, source_id)

    async def close(self) -> None:
        return None


class _MemorySimhashIndex(ISimhashIndex):
    def __init__(self) -> None:
        self._items: list[int] = []

    async def find_recent(self, since: datetime) -> list[int]:
        return list(self._items)

    async def save(self, simhash: int, source_id: str, external_id: str) -> None:
        self._items.append(simhash)

    async def purge_older_than(self, cutoff: datetime) -> int:
        return 0

    async def close(self) -> None:
        return None


class _MemoryLemmaIndex(ILemmaSetIndex):
    def __init__(self) -> None:
        self._items: list[frozenset[str]] = []

    async def find_recent(self, since: datetime) -> list[frozenset[str]]:
        return list(self._items)

    async def save(self, lemmas: frozenset[str], source_id: str, external_id: str) -> None:
        self._items.append(lemmas)

    async def purge_older_than(self, cutoff: datetime) -> int:
        return 0

    async def close(self) -> None:
        return None


class _MemoryImageHashIndex(IImageHashIndex):
    async def find_recent(self, since: datetime) -> list[ImageHashRecord]:
        return []

    async def save(self, record: ImageHashRecord, external_id: str) -> None:
        return None

    async def purge_older_than(self, cutoff: datetime) -> int:
        return 0


def _message(text: str, ident: str) -> ProcessedMessage:
    normalized = normalize_text(text)
    now = datetime.now(UTC)
    raw = RawMessage(
        source_id="explain", external_id=ident, text=text, posted_at=now, fetched_at=now
    )
    return ProcessedMessage(
        raw=raw,
        normalized_text=normalized,
        text_hash=compute_text_hash(normalized),
        links=extract_links(text),
    )


class _MemoryEmbeddingIndex(IEmbeddingIndex):
    def __init__(self) -> None:
        self._items: list[EmbeddingRecord] = []

    async def find_recent(self, since: datetime) -> list[EmbeddingRecord]:
        return list(self._items)

    async def save(self, record: EmbeddingRecord, external_id: str) -> None:
        self._items.append(record)

    async def purge_older_than(self, cutoff: datetime) -> int:
        return 0


# Модель в Docker-образе; для запуска вне контейнера model_dir задаётся в config.yaml.
_MODEL_DIR = "/app/models/paraphrase-multilingual-MiniLM-L12-v2"


class _SnapshotFrequencyIndex(IStemFrequencyIndex):
    """Частоты слов из боевой базы, открытой только на чтение; add() ничего не пишет."""

    def __init__(self, db_path: str | None, table_name: str = "stem_frequencies") -> None:
        self._db_path = db_path
        self._table_name = table_name

    async def load(self) -> tuple[int, dict[str, int]]:
        if self._db_path is None:
            return 0, {}
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                f"SELECT stem, documents FROM {self._table_name}"  # noqa: S608
            ).fetchall()
        finally:
            conn.close()
        counts = {str(stem): int(documents) for stem, documents in rows}
        return counts.pop("", 0), counts

    async def add(self, stems: frozenset[str]) -> None:
        return None


def _build_layers(config_path: str, frequencies_db: str | None = None) -> list[IDeduplicator]:
    config = load_app_config(config_path)
    registry = build_registry()
    storage = _MemoryStorage()
    simhash_index = _MemorySimhashIndex()
    lemma_index = _MemoryLemmaIndex()
    layers: list[IDeduplicator] = []
    for component in config.deduplicators:
        if component.name == "simhash_deduplicator":
            kwargs: dict[str, object] = {"index": simhash_index}
        elif component.name == "paraphrase_deduplicator":
            kwargs = {"index": lemma_index}
            params = dict(component.params)
            table = str(params.pop("frequency_table", "stem_frequencies"))
            if params.get("weighted_min_jaccard") is not None:
                kwargs["frequency_index"] = _SnapshotFrequencyIndex(frequencies_db, table)
            created = registry.deduplicators.create(component.name, **kwargs, **params)
            layers.append(cast(IDeduplicator, created))
            continue
        elif component.name == "embedding_deduplicator":
            params = dict(component.params)
            params.pop("index_name", None)
            model_dir = str(params.pop("model_dir", _MODEL_DIR))
            embedder = None
            if "onnx" in registry.text_embedders.available() and os.path.isdir(model_dir):
                embedder = registry.text_embedders.create("onnx", model_dir=model_dir)
            created = registry.deduplicators.create(
                component.name, index=_MemoryEmbeddingIndex(), embedder=embedder, **params
            )
            layers.append(cast(IDeduplicator, created))
            continue
        elif component.name == "image_deduplicator":
            # Сравниваются только тексты: картинок у dedup_explain нет.
            kwargs = {"index": _MemoryImageHashIndex(), "hasher": None}
        else:
            kwargs = {"storage": storage}
        created = registry.deduplicators.create(component.name, **kwargs, **component.params)
        layers.append(cast(IDeduplicator, created))
    return layers


def _lemma_details(first: str, second: str, layer: object) -> list[str]:
    stem_length = getattr(layer, "_stem_length", 5)
    lemmas_a = extract_lemma_set(normalize_text(first))
    lemmas_b = extract_lemma_set(normalize_text(second))
    stems_a, stems_b = stem_set(lemmas_a, stem_length), stem_set(lemmas_b, stem_length)
    shared = sorted(stems_a & stems_b)
    return [
        f"    леммы A ({len(lemmas_a)}): {', '.join(sorted(lemmas_a)) or '—'}",
        f"    леммы B ({len(lemmas_b)}): {', '.join(sorted(lemmas_b)) or '—'}",
        f"    общие основы ({len(shared)}): {', '.join(shared) or '—'}",
        f"    числа A: {', '.join(sorted(numbers_in(lemmas_a))) or '—'}; "
        f"числа B: {', '.join(sorted(numbers_in(lemmas_b))) or '—'}",
    ]


async def explain(
    first: str, second: str, config_path: str, frequencies_db: str | None = None
) -> tuple[str, bool]:
    """Возвращает (текст отчёта, признан ли второй текст дублем хотя бы одним слоем)."""
    layers = _build_layers(config_path, frequencies_db)
    published, candidate = _message(first, "A"), _message(second, "B")
    for layer in layers:
        await layer.remember(published)

    lines = [
        f"A (считается опубликованным): {first.strip()[:120]}",
        f"B (проверяется):              {second.strip()[:120]}",
        f"нормализовано A: {published.normalized_text}",
        f"нормализовано B: {candidate.normalized_text}",
        "",
    ]
    first_hit: str | None = None
    for layer in layers:
        result = await layer.check(candidate)
        verdict = "ДУБЛЬ" if result.is_duplicate else "не дубль"
        lines.append(f"{layer.name:26} {verdict:9} {result.reason}")
        if layer.name == "paraphrase_deduplicator":
            lines.extend(_lemma_details(first, second, layer))
        if result.is_duplicate and first_hit is None:
            first_hit = layer.name

    lines.append("")
    if first_hit:
        lines.append(f"ИТОГ: дубль (первым сработал {first_hit}) — B не будет опубликован")
    else:
        lines.append("ИТОГ: не дубль — B будет опубликован")
    return "\n".join(lines), first_hit is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="news_aggregator.dedup_explain",
        description="Показывает, как дедупликаторы оценивают пару текстов",
    )
    parser.add_argument("first", help="текст уже опубликованного поста (A)")
    parser.add_argument("second", help="новый текст, который проверяется (B)")
    parser.add_argument("--config", default="config/config.yaml", help="Путь к config.yaml")
    parser.add_argument(
        "--frequencies",
        default=None,
        help="База с частотами слов для весового правила (например data/state.db), только чтение",
    )
    args = parser.parse_args(argv)
    try:
        report, _ = asyncio.run(explain(args.first, args.second, args.config, args.frequencies))
    except (ConfigError, ValueError, sqlite3.Error) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
