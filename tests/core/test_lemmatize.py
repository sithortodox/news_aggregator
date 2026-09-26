from news_aggregator.core.lemmatize import extract_lemma_set, jaccard_similarity
from news_aggregator.core.text_utils import normalize_text


def test_extract_lemma_set_empty_text_is_empty() -> None:
    assert extract_lemma_set("") == frozenset()


def test_extract_lemma_set_normalizes_word_forms_to_same_lemma() -> None:
    """Разные словоформы одного слова должны давать одну и ту же лемму —
    это и есть весь смысл модуля."""
    a = extract_lemma_set(normalize_text("Центробанк повысил ключевую ставку"))
    b = extract_lemma_set(normalize_text("решение о повышении ключевой ставки"))
    assert "ставка" in a
    assert "ставка" in b


def test_extract_lemma_set_drops_stopwords() -> None:
    lemmas = extract_lemma_set(normalize_text("и в на с по для"))
    assert lemmas == frozenset()


def test_extract_lemma_set_drops_single_char_tokens() -> None:
    lemmas = extract_lemma_set("а б новость")
    assert "а" not in lemmas
    assert "б" not in lemmas


def test_extract_lemma_set_keeps_digits() -> None:
    lemmas = extract_lemma_set(normalize_text("ставка выросла до 18 процентов"))
    assert "18" in lemmas


def test_jaccard_similarity_identical_sets_is_one() -> None:
    s = frozenset({"а", "б", "в"})
    assert jaccard_similarity(s, s) == 1.0


def test_jaccard_similarity_disjoint_sets_is_zero() -> None:
    assert jaccard_similarity(frozenset({"а"}), frozenset({"б"})) == 0.0


def test_jaccard_similarity_empty_sets_is_zero() -> None:
    assert jaccard_similarity(frozenset(), frozenset()) == 0.0
    assert jaccard_similarity(frozenset({"а"}), frozenset()) == 0.0


def test_jaccard_similarity_partial_overlap() -> None:
    a = frozenset({"а", "б", "в"})
    b = frozenset({"б", "в", "г"})
    # пересечение {б, в} = 2, объединение {а,б,в,г} = 4
    assert jaccard_similarity(a, b) == 0.5


def test_paraphrased_news_from_different_channels_have_high_jaccard() -> None:
    """Ключевой сценарий: одна и та же новость, пересказанная разными
    словами разными каналами, должна давать заметно ненулевое пересечение
    лемм — то, что SimHash принципиально не ловит (см. paraphrase_deduplicator)."""
    a = extract_lemma_set(
        normalize_text(
            "Центробанк России повысил ключевую ставку до 18 процентов на "
            "сегодняшнем заседании совета директоров"
        )
    )
    b = extract_lemma_set(
        normalize_text(
            "Сегодня ЦБ РФ принял решение о повышении ключевой ставки — теперь она составляет 18%"
        )
    )
    score = jaccard_similarity(a, b)
    assert score >= 0.15, f"score={score} для пересказа одной новости"


def test_unrelated_news_have_zero_or_near_zero_jaccard() -> None:
    a = extract_lemma_set(
        normalize_text(
            "Центробанк России повысил ключевую ставку до 18 процентов на "
            "сегодняшнем заседании совета директоров"
        )
    )
    b = extract_lemma_set(
        normalize_text(
            "Учёные обнаружили новый вид бабочек в тропических лесах "
            "Амазонии, ранее науке неизвестный"
        )
    )
    score = jaccard_similarity(a, b)
    assert score <= 0.05, f"score={score} для НЕсвязанных новостей"
