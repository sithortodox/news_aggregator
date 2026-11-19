"""SHA-256 content-hash для файлов/медиа (паттерн content-hash-cache).

См. скилл `content-hash-cache-pattern` (affaan-m/ecc): кэш результатов
обработки файлов строится по SHA-256 контента, а не по пути — файл переименован
или одинаковый контент у разных источников = одна и та же обработка.
"""

from __future__ import annotations

import hashlib


def compute_file_hash(data: bytes, chunk_size: int = 65536) -> str:
    """SHA-256 (hex) контента данных. Чанковая обработка — для больших файлов."""
    sha256 = hashlib.sha256()
    for i in range(0, len(data), chunk_size):
        sha256.update(data[i : i + chunk_size])
    return sha256.hexdigest()
