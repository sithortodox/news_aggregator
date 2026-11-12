"""Перцептивный хэш картинки (dHash) и расстояние между хэшами.

dHash сравнивает яркость соседних пикселей уменьшенной серой копии картинки:
он устойчив к пережатию JPEG и небольшому изменению размера, то есть к тому,
что происходит, когда два канала по отдельности загружают один и тот же
скриншот. Размер 16 даёт 256 бит: на 64 битах (размер 8) разные однотипные
картинки, например светлые скриншоты постов, слишком часто совпадают.

Порог по умолчанию в ImageDeduplicator подобран на 455 картинках из 25 каналов
агрегатора: одна и та же картинка в разных каналах — расстояние 0–17, разные
картинки — от 30, в основном больше 60.
"""

from __future__ import annotations

import io

from PIL import Image, ImageStat

HASH_SIZE = 16

# Ниже этого разброса яркости (стандартное отклонение, 0–255) картинка считается
# пустой: чёрный или белый кадр, однотонная заливка. У таких картинок dHash
# состоит из шума сжатия, и разные видео, начинающиеся с чёрного кадра,
# совпали бы между собой.
MIN_CONTRAST = 5.0


def dhash(data: bytes, hash_size: int = HASH_SIZE) -> int:
    """dHash картинки из байтов файла (JPEG, PNG и т.п.): hash_size² бит."""
    with Image.open(io.BytesIO(data)) as image:
        gray = image.convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
        pixels = gray.tobytes()
    value = 0
    width = hash_size + 1
    for row in range(hash_size):
        offset = row * width
        for col in range(hash_size):
            value = (value << 1) | (pixels[offset + col] > pixels[offset + col + 1])
    return value


def informative_dhash(data: bytes, hash_size: int = HASH_SIZE) -> int | None:
    """dHash картинки или None, если картинка почти однотонная и сравнивать нечего."""
    with Image.open(io.BytesIO(data)) as image:
        contrast = ImageStat.Stat(image.convert("L")).stddev[0]
    if contrast < MIN_CONTRAST:
        return None
    return dhash(data, hash_size)


def hamming_distance(first: int, second: int) -> int:
    """Число различающихся бит."""
    return (first ^ second).bit_count()
