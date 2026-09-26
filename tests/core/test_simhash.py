from news_aggregator.core.simhash import compute_simhash, hamming_distance
from news_aggregator.core.text_utils import normalize_text


def test_compute_simhash_is_deterministic() -> None:
    assert compute_simhash("привет мир") == compute_simhash("привет мир")


def test_compute_simhash_empty_text_is_zero() -> None:
    assert compute_simhash("") == 0


def test_compute_simhash_handles_text_shorter_than_shingle_size() -> None:
    # Не должно падать, даже если текст короче размера шингла.
    result = compute_simhash("ok", shingle_size=4)
    assert isinstance(result, int)


def test_compute_simhash_result_fits_in_requested_bits() -> None:
    result = compute_simhash("любой текст для проверки диапазона", bits=64)
    assert 0 <= result < (1 << 64)


def test_hamming_distance_zero_for_identical_hashes() -> None:
    h = compute_simhash("одна и та же строка")
    assert hamming_distance(h, h) == 0


def test_hamming_distance_is_symmetric() -> None:
    a = compute_simhash("текст номер один")
    b = compute_simhash("текст номер два, немного другой")
    assert hamming_distance(a, b) == hamming_distance(b, a)


def test_near_duplicate_texts_have_small_hamming_distance() -> None:
    """Почти дословные повторы (декор, знаки препинания, короткая приписка
    источника) должны давать маленькое расстояние Хэмминга."""
    pairs = [
        (
            "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании",
            "Центробанк  повысил ключевую ставку до 18 процентов  сегодня утром на заседании.",
        ),
        (
            "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании",
            "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании (РБК)",
        ),
        (
            "Открылась новая линия метро в центре города, движение поездов началось утром",
            "Открылась новая линия метро в центре города! Движение поездов началось утром 🚇",
        ),
    ]
    for text_a, text_b in pairs:
        hash_a = compute_simhash(normalize_text(text_a))
        hash_b = compute_simhash(normalize_text(text_b))
        distance = hamming_distance(hash_a, hash_b)
        assert distance <= 5, f"distance={distance} для похожих текстов: {text_a!r} vs {text_b!r}"


def test_unrelated_texts_have_large_hamming_distance() -> None:
    """Новости о разных событиях должны давать расстояние, значительно
    превышающее порог дублирования (иначе SimHash бесполезен)."""
    pairs = [
        (
            "Центробанк повысил ключевую ставку до 18 процентов сегодня утром",
            "Учёные обнаружили новый вид бабочек в тропических лесах Амазонии",
        ),
        (
            "Открылась новая линия метро в центре города",
            "Скидка 50% на все товары только сегодня, промокод внутри",
        ),
        (
            "Курс доллара вырос на 2 рубля к вечеру торгов на Московской бирже",
            "Президент подписал указ о повышении пенсий с 1 января следующего года",
        ),
    ]
    for text_a, text_b in pairs:
        hash_a = compute_simhash(normalize_text(text_a))
        hash_b = compute_simhash(normalize_text(text_b))
        distance = hamming_distance(hash_a, hash_b)
        assert distance > 10, (
            f"distance={distance} для НЕсвязанных текстов: {text_a!r} vs {text_b!r}"
        )


def test_different_shingle_sizes_still_deterministic() -> None:
    text = normalize_text("Проверка разных размеров шингла на одном и том же тексте")
    for size in (2, 3, 4, 5):
        h1 = compute_simhash(text, shingle_size=size)
        h2 = compute_simhash(text, shingle_size=size)
        assert h1 == h2


def test_word_unit_is_deterministic() -> None:
    text = normalize_text("привет мир как дела сегодня")
    assert compute_simhash(text, unit="word") == compute_simhash(text, unit="word")


def test_word_unit_empty_text_is_zero() -> None:
    assert compute_simhash("", unit="word") == 0


def test_word_unit_handles_text_shorter_than_shingle_size() -> None:
    result = compute_simhash("одно слово", shingle_size=5, unit="word")
    assert isinstance(result, int)


def test_word_unit_differs_from_char_unit_for_same_text() -> None:
    """char и word — разные алгоритмы, отпечатки для одного текста не
    обязаны (и в общем случае не будут) совпадать."""
    text = normalize_text("привет как у тебя сегодня дела дружище")
    char_hash = compute_simhash(text, unit="char")
    word_hash = compute_simhash(text, unit="word")
    assert char_hash != word_hash


def test_word_unit_near_verbatim_repost_has_small_distance() -> None:
    """word-режим по-прежнему должен ловить почти-дословные повторы —
    основное назначение SimHash независимо от unit."""
    a = normalize_text(
        "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании"
    )
    b = normalize_text(
        "Центробанк повысил ключевую ставку до 18 процентов сегодня утром на заседании (РБК)"
    )
    hash_a = compute_simhash(a, unit="word", shingle_size=2)
    hash_b = compute_simhash(b, unit="word", shingle_size=2)
    assert hamming_distance(hash_a, hash_b) <= 10
