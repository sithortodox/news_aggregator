"""SimHash — вычисление отпечатка текста для поиска почти-дублей.

Чистые функции без побочных эффектов и внешних зависимостей — как и
text_utils.py. Используются поверх уже нормализованного текста
(news_aggregator.core.text_utils.normalize_text), а не сырого.

Алгоритм — классический SimHash Чарикара на посимвольных шинглах:
    1. Текст режется на перекрывающиеся шинглы фиксированной длины (n-граммы
       символов). Символьные шинглы выбраны вместо пословных — устойчивее
       на коротких текстах (заголовки/посты в несколько предложений) и не
       требует токенизации по языку.
    2. Каждый шингл хэшируется в стабильное N-битное число (hashlib, а не
       встроенный hash() — тот рандомизирован между запусками процесса).
    3. Для каждого бита каждого хэша: бит=1 добавляет вес в соответствующую
       позицию вектора, бит=0 — вычитает. Повторяющиеся шинглы усиливают
       вес (учитываются как отдельные слагаемые).
    4. Итоговый бит i отпечатка = 1, если сумма по позиции i положительна.

Похожие тексты (отличаются несколькими словами/порядком) дают отпечатки с
маленьким расстоянием Хэмминга; непохожие — близким к половине битности
(для 64 бит — около 32).
"""

from __future__ import annotations

import hashlib
from collections import Counter

DEFAULT_BITS = 64
DEFAULT_SHINGLE_SIZE = 3


def _shingles(text: str, shingle_size: int) -> Counter[str]:
    """Разбивает текст на перекрывающиеся символьные шинглы с учётом частоты.

    Если текст короче shingle_size, весь текст считается одним шинглом —
    иначе короткие посты (например, после нормализации) вообще не дали бы
    ни одного шингла.
    """
    if len(text) <= shingle_size:
        return Counter([text]) if text else Counter()
    return Counter(text[i : i + shingle_size] for i in range(len(text) - shingle_size + 1))


def _hash_shingle(shingle: str, bits: int) -> int:
    """Хэширует шингл в стабильное (не зависящее от запуска) N-битное число."""
    digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=(bits + 7) // 8).digest()
    return int.from_bytes(digest, byteorder="big") & ((1 << bits) - 1)


def compute_simhash(
    text: str, *, shingle_size: int = DEFAULT_SHINGLE_SIZE, bits: int = DEFAULT_BITS
) -> int:
    """Считает SimHash-отпечаток текста как целое число из `bits` бит.

    Args:
        text: Текст (ожидается уже нормализованный, см. text_utils.normalize_text).
        shingle_size: Длина символьных n-грамм.
        bits: Разрядность отпечатка.

    Пустой текст даёт отпечаток 0 — вызывающий код должен решить, что с
    этим делать (пустые/бессмысленные сообщения обычно уже отсеяны
    фильтрами до вычисления SimHash).
    """
    shingle_counts = _shingles(text, shingle_size)
    if not shingle_counts:
        return 0

    weights = [0] * bits
    for shingle, count in shingle_counts.items():
        shingle_hash = _hash_shingle(shingle, bits)
        for bit_index in range(bits):
            if (shingle_hash >> bit_index) & 1:
                weights[bit_index] += count
            else:
                weights[bit_index] -= count

    result = 0
    for bit_index in range(bits):
        if weights[bit_index] > 0:
            result |= 1 << bit_index
    return result


def hamming_distance(a: int, b: int) -> int:
    """Считает расстояние Хэмминга между двумя отпечатками (число разных бит)."""
    return (a ^ b).bit_count()
