from news_aggregator.core.text_utils import (
    compute_text_hash,
    extract_links,
    normalize_text,
)


def test_normalize_text_lowercases_and_strips_punctuation() -> None:
    assert normalize_text("Привет,  Мир!!!") == "привет мир"


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("hello   \n\t world") == "hello world"


def test_normalize_text_removes_links() -> None:
    text = "Смотрите новость: https://example.com/news/123 подробнее там"
    assert "example" not in normalize_text(text)


def test_normalize_text_is_idempotent() -> None:
    text = "Some MIXED Текст with Punctuation!!"
    once = normalize_text(text)
    twice = normalize_text(once)
    assert once == twice


def test_extract_links_finds_http_and_www_and_tme() -> None:
    text = "Раз https://example.com/a два www.example.org/b три t.me/some_channel/42 конец."
    links = extract_links(text)
    assert links == (
        "https://example.com/a",
        "www.example.org/b",
        "t.me/some_channel/42",
    )


def test_extract_links_deduplicates_and_strips_trailing_punctuation() -> None:
    text = "Ссылка (https://example.com/a). Ещё раз: https://example.com/a!"
    links = extract_links(text)
    assert links == ("https://example.com/a",)


def test_extract_links_empty_when_no_links() -> None:
    assert extract_links("просто текст без ссылок") == ()


def test_compute_text_hash_is_deterministic() -> None:
    h1 = compute_text_hash("привет мир")
    h2 = compute_text_hash("привет мир")
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest length


def test_compute_text_hash_differs_for_different_text() -> None:
    assert compute_text_hash("текст а") != compute_text_hash("текст б")


def test_same_news_different_wording_can_share_hash_after_normalization() -> None:
    # Ссылки не влияют на хэш, регистр и пунктуация — тоже.
    a = normalize_text("Курс доллара вырос! Подробнее: https://a.com/x")
    b = normalize_text("курс доллара вырос подробнее: https://b.com/y")
    assert compute_text_hash(a) == compute_text_hash(b)


def test_normalize_text_strips_hashtags_entirely() -> None:
    text = "Курс доллара вырос #экономика #новости"
    assert "экономика" not in normalize_text(text)
    assert "новости" not in normalize_text(text)
    assert normalize_text(text) == "курс доллара вырос"


def test_normalize_text_strips_mentions_entirely() -> None:
    text = "Смотрите также @some_channel про этот случай"
    normalized = normalize_text(text)
    assert "some_channel" not in normalized
    assert normalized == "смотрите также про этот случай"


def test_normalize_text_collapses_stretched_repeated_chars() -> None:
    assert normalize_text("Оооочень интересная новость!!!!!") == "оочень интересная новость"


def test_normalize_text_keeps_legitimate_double_letters() -> None:
    # "касса", "русский" - двойные буквы не должны схлопываться (порог: 3+).
    assert normalize_text("Русская касса выросла") == "русская касса выросла"


def test_normalize_text_with_hashtag_and_mention_matches_plain_version() -> None:
    # Реалистичный кейс: один и тот же текст, но один канал добавил хэштег
    # и упоминание при репосте — это не должно мешать дедупликации.
    plain = "Курс доллара резко вырос сегодня утром"
    decorated = "Курс доллара резко вырос сегодня утром #экономика @repost_channel"
    assert normalize_text(plain) == normalize_text(decorated)
