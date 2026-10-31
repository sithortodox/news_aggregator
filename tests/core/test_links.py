import pytest

from news_aggregator.core.links import canonical_content_links, canonicalize_url

ARTICLE = "example.com/news/123"


@pytest.mark.parametrize(
    "variant",
    [
        "https://example.com/news/123",
        "http://example.com/news/123",
        "https://www.example.com/news/123",
        "HTTPS://EXAMPLE.COM/news/123",
        "https://example.com/news/123/",
        "https://example.com/news/123#comments",
        "https://example.com/news/123?utm_source=telegram&utm_medium=social",
        "https://example.com/news/123?fbclid=IwAR0abc",
        "https://example.com/news/123?gclid=x&yclid=y&ysclid=z",
        "example.com/news/123",
        "  https://example.com/news/123  ",
        "https://example.com./news/123",
        "https://example.com:443/news/123",
        "https://example.com//news///123",
    ],
)
def test_variants_of_the_same_article_share_one_canonical_form(variant: str) -> None:
    assert canonicalize_url(variant) == ARTICLE


def test_different_articles_stay_different() -> None:
    assert canonicalize_url("https://example.com/news/123") != canonicalize_url(
        "https://example.com/news/124"
    )
    assert canonicalize_url("https://example.com/a") != canonicalize_url("https://other.com/a")


def test_path_case_is_preserved_because_paths_are_case_sensitive() -> None:
    assert canonicalize_url("https://example.com/News/ABC") == "example.com/News/ABC"


def test_meaningful_query_params_are_kept_and_sorted() -> None:
    one = canonicalize_url("https://example.com/read?id=5&page=2&utm_source=x")
    two = canonicalize_url("https://example.com/read?page=2&id=5")

    assert one == two == "example.com/read?id=5&page=2"
    assert canonicalize_url("https://example.com/read?id=6") != one


def test_non_default_port_is_kept() -> None:
    assert canonicalize_url("https://example.com:8080/a") == "example.com:8080/a"


@pytest.mark.parametrize(
    "variant",
    [
        "t.me/some_channel/42",
        "https://t.me/some_channel/42",
        "https://t.me/some_channel/42?single",
        "https://t.me/Some_Channel/42?comment=7",
        "https://telegram.me/some_channel/42",
        "https://t.me/s/some_channel/42",
    ],
)
def test_telegram_post_links_share_one_canonical_form(variant: str) -> None:
    assert canonicalize_url(variant) == "t.me/some_channel/42"


def test_private_channel_post_link() -> None:
    assert canonicalize_url("https://t.me/c/1234567/89") == "t.me/c/1234567/89"


@pytest.mark.parametrize(
    "not_content",
    [
        "https://t.me/some_channel",  # профиль канала — подпись
        "t.me/some_channel/",
        "https://t.me/+AbCdEfGh",  # приглашение
        "https://t.me/joinchat/AAAA",
        "https://t.me/some_bot?start=ref",
        "https://example.com",  # главная страница сайта
        "https://example.com/",
        "https://www.example.com/?utm_source=x",
        "",
        "   ",
        "http://",
        "https://[broken",
    ],
)
def test_links_that_are_not_specific_content_are_dropped(not_content: str) -> None:
    assert canonicalize_url(not_content) is None


def test_canonical_content_links_keeps_order_and_drops_repeats_and_non_content() -> None:
    links = (
        "https://t.me/some_channel",
        "https://example.com/a?utm_source=x",
        "https://www.example.com/a/",
        "https://example.com/b",
    )

    assert canonical_content_links(links) == (
        ("https://example.com/a?utm_source=x", "example.com/a"),
        ("https://example.com/b", "example.com/b"),
    )
