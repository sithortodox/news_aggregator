"""SimHash — вычисление отпечатка текста для поиска почти-дублей.

Чистые функции без побочных эффектов и внешних зависимостей — как и
text_utils.py. Используются поверх уже нормализованного текста
(news_aggregator.core.text_utils.normalize_text), а не сырого.

Поддерживаются два режима шинглов (см. `unit` в compute_simhash):
    - "char" (по умолчанию) — посимвольные n-граммы. Устойчивее на
      коротких текстах и не требует токенизации по языку, но ловит только
      почти-дословные повторы (см. docstring SimHashDeduplicator).
    - "word" — пословные n-граммы (n подряд идущих слов). Менее
      чувствителен к точной формулировке: два repost'а одной новости
      разными словами, но с общими именами/цифрами/ключевыми фразами,
      скорее дадут близкие отпечатки, чем при посимвольном режиме. Именно
      это нужно для дедупликации одной и той же новости, пересказанной
      разными каналами разными словами — то, что посимвольный SimHash
      принципиально не ловит.

Алгоритм — классический SimHash Чарикара:
    1. Текст режется на перекрывающиеся шинглы (символьные или пословные
       n-граммы, см. выше).
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
from typing import Literal

DEFAULT_BITS = 64
DEFAULT_SHINGLE_SIZE = 3

SimhashUnit = Literal["char", "word"]


def _char_shingles(text: str, shingle_size: int) -> Counter[str]:
    """Разбивает текст на перекрывающиеся символьные шинглы с учётом частоты.

    Если текст короче shingle_size, весь текст считается одним шинглом —
    иначе короткие посты (например, после нормализации) вообще не дали бы
    ни одного шингла.
    """
    if len(text) <= shingle_size:
        return Counter([text]) if text else Counter()
    return Counter(text[i : i + shingle_size] for i in range(len(text) - shingle_size + 1))


def _word_shingles(text: str, shingle_size: int) -> Counter[str]:
    """Разбивает текст на перекрывающиеся пословные шинглы (n подряд слов).

    Слова — это то, что осталось после разбиения по пробелам; текст
    ожидается уже нормализованным (пунктуация/эмодзи убраны), поэтому
    простого split() достаточно, без языкозависимой токенизации.
    """
    words = text.split()
    if not words:
        return Counter()
    if len(words) <= shingle_size:
        return Counter([" ".join(words)])
    return Counter(
        " ".join(words[i : i + shingle_size]) for i in range(len(words) - shingle_size + 1)
    )


def _shingles(text: str, shingle_size: int, unit: SimhashUnit) -> Counter[str]:
    if unit == "word":
        return _word_shingles(text, shingle_size)
    return _char_shingles(text, shingle_size)


def _hash_shingle(shingle: str, bits: int) -> int:
    """Хэширует шингл в стабильное (не зависящее от запуска) N-битное число."""
    digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=(bits + 7) // 8).digest()
    return int.from_bytes(digest, byteorder="big") & ((1 << bits) - 1)


def compute_simhash(
    text: str,
    *,
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
    bits: int = DEFAULT_BITS,
    unit: SimhashUnit = "char",
) -> int:
    """Считает SimHash-отпечаток текста как целое число из `bits` бит.

    Args:
        text: Текст (ожидается уже нормализованный, см. text_utils.normalize_text).
        shingle_size: Длина n-грамм (символов или слов — см. unit).
        bits: Разрядность отпечатка.
        unit: "char" — посимвольные шинглы (почти-дословные повторы),
            "word" — пословные шинглы (устойчивее к перефразированию).

    Пустой текст даёт отпечаток 0 — вызывающий код должен решить, что с
    этим делать (пустые/бессмысленные сообщения обычно уже отсеяны
    фильтрами до вычисления SimHash).
    """
    shingle_counts = _shingles(text, shingle_size, unit)
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
