import pytest

from news_aggregator.core.lemmatize import STOPWORDS, extract_lemma_set
from news_aggregator.core.text_utils import normalize_text


@pytest.mark.parametrize("word", ["про", "через", "между", "после", "перед", "очень"])
def test_function_words_are_stopwords(word: str) -> None:
    assert word in STOPWORDS


# Стоп-слова сравниваются с ЛЕММАМИ: «подробнее» лемматизируется в «подробный».
@pytest.mark.parametrize(
    "lemma",
    ["подписаться", "новость", "читать", "подробный", "подробность", "источник", "фото", "видео"],
)
def test_channel_boilerplate_lemmas_are_stopwords(lemma: str) -> None:
    assert lemma in STOPWORDS


@pytest.mark.parametrize("word", ["реклама", "канал", "два", "закон", "ставка", "суд"])
def test_words_that_can_be_the_subject_of_a_story_are_not_stopwords(word: str) -> None:
    """«Закон о запрете рекламы» и «канал связи» — это содержание, а не обвязка."""
    assert word not in STOPWORDS


def test_boilerplate_is_dropped_from_lemma_sets() -> None:
    lemmas = extract_lemma_set(normalize_text("Новость про хоккей. Подписаться, читать подробнее"))

    assert lemmas == frozenset({"хоккей"})


def test_story_words_survive_lemmatization() -> None:
    lemmas = extract_lemma_set(normalize_text("Госдума приняла закон о запрете рекламы"))

    assert {"реклама", "закон", "запрет"} <= lemmas
