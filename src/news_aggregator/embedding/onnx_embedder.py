"""ITextEmbedder на модели paraphrase-multilingual-MiniLM-L12-v2 (ONNX, int8).

Модель выбрана замером на 1100 постах ленты: из двух небольших
многоязычных моделей она лучше отделяет пересказы одного события от
соседних новостей одной темы (multilingual-e5-small даёт почти всем парам
косинус 0.8–0.95).

Память — главное ограничение VPS (1 ГБ). Квантованная модель в onnxruntime
занимает ~130 МБ. Токенизатор берётся из sentencepiece (~55 МБ), а не из
пакета tokenizers: тот же словарь там занимает ~280 МБ. Токены совпадают с
эталонным токенизатором модели: XLM-R сдвигает номера sentencepiece на 1,
<unk> — 3, текст обрамляется <s> (0) и </s> (2). Длина обрезается до 128
токенов, как при обучении модели.

Модель загружается при первом вызове embed(), а не при старте: если слой
выключен в конфиге, память не тратится.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from news_aggregator.core.interfaces import ITextEmbedder

_MAX_TOKENS = 128
_BOS, _EOS, _UNK = 0, 2, 3


class OnnxTextEmbedder(ITextEmbedder):
    def __init__(self, model_dir: str) -> None:
        path = Path(model_dir)
        self._model_path = path / "model_quantized.onnx"
        self._tokenizer_path = path / "sentencepiece.bpe.model"
        missing = [p.name for p in (self._model_path, self._tokenizer_path) if not p.exists()]
        if missing:
            raise FileNotFoundError(f"В {model_dir} нет файлов модели: {', '.join(missing)}")
        self._lock = threading.Lock()
        self._session: Any = None
        self._tokenizer: Any = None

    def _load(self) -> None:
        import onnxruntime
        import sentencepiece

        options = onnxruntime.SessionOptions()
        # Одно ядро на сервере: лишние потоки только мешают остальному процессу.
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._session = onnxruntime.InferenceSession(
            str(self._model_path), options, providers=["CPUExecutionProvider"]
        )
        self._tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(self._tokenizer_path))

    def _token_ids(self, text: str) -> list[int]:
        pieces = [piece + 1 if piece else _UNK for piece in self._tokenizer.encode(text)]
        return [_BOS, *pieces[: _MAX_TOKENS - 2], _EOS]

    def embed(self, text: str) -> tuple[float, ...]:
        import numpy as np

        with self._lock:
            if self._session is None:
                self._load()
            ids = np.array([self._token_ids(text)], dtype=np.int64)
            mask = np.ones_like(ids)
            output = self._session.run(
                None,
                {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)},
            )[0][0]
        # Среднее по токенам (так модель обучена), затем нормировка.
        vector = output.mean(axis=0)
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            return tuple(0.0 for _ in range(len(vector)))
        return tuple(float(x) for x in vector / norm)
