import pytest

from news_aggregator.core.lemmatize import STOPWORDS, extract_lemma_set, is_stopword
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


# ------------------------------------------------------------- е и ё не различаются


@pytest.mark.parametrize("word", ["её", "ещё", "всё", "её", "еще", "ее"])
def test_stopwords_are_filtered_regardless_of_e_yo_spelling(word: str) -> None:
    """pymorphy возвращает «её»/«ещё» с ё, список стоп-слов — с е: раньше такие слова
    не отфильтровывались и попадали в множество лемм как значимые."""
    assert is_stopword(word) or is_stopword(word.replace("ё", "е"))
    assert word not in extract_lemma_set(normalize_text(f"Хоккей {word} финал"))


def test_content_words_with_yo_are_not_affected() -> None:
    lemmas = extract_lemma_set(normalize_text("Учёные нашли новый вид бабочек"))

    assert "учёный" in lemmas


@pytest.mark.parametrize("word", ["лишь", "просто", "мочь", "чей"])
def test_generic_modal_and_particle_words_are_stopwords(word: str) -> None:
    assert word in STOPWORDS
