"""Скачивает файлы модели для OnnxTextEmbedder и проверяет их SHA-256.

    python download.py /app/models

Без зависимостей (только стандартная библиотека): запускается в Dockerfile
до установки пакета, чтобы слой с моделью кэшировался отдельно от кода.
Ревизии закреплены: модель не поменяется сама при пересборке образа.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# (URL, имя файла, SHA-256)
FILES = (
    (
        "https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/"
        "2c4055b12046f11709e9df2c122e59ffbdc2f900/onnx/model_quantized.onnx",
        "model_quantized.onnx",
        "66fc00f5f29afcaff34092e1bdd20008ca3918265a82fb9695a551e510cc4ebc",
    ),
    (
        "https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/"
        "resolve/e8f8c211226b894fcb81acc59f3b34ba3efd5f42/sentencepiece.bpe.model",
        "sentencepiece.bpe.model",
        "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(dest: Path) -> Path:
    model_dir = dest / MODEL_NAME
    model_dir.mkdir(parents=True, exist_ok=True)
    for url, name, expected in FILES:
        target = model_dir / name
        if target.exists() and _sha256(target) == expected:
            continue
        partial = target.with_suffix(target.suffix + ".part")
        urllib.request.urlretrieve(url, partial)  # noqa: S310 — закреплённый https-адрес
        actual = _sha256(partial)
        if actual != expected:
            partial.unlink()
            raise SystemExit(f"{name}: SHA-256 {actual} не совпадает с ожидаемым {expected}")
        partial.replace(target)
    return model_dir


if __name__ == "__main__":
    print(download(Path(sys.argv[1] if len(sys.argv) > 1 else "models")))
