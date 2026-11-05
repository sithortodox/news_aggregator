"""Каноническая форма ссылок для дедупликации.

Одна и та же статья в постах разных каналов записывается по-разному:
с `utm_*`-метками, с/без `www`, по http/https, со слешем на конце, с `?single`
у ссылок на посты Telegram. Сравнение сырых строк считает это разными
ссылками. Здесь ссылка приводится к канонической форме.

Вторая половина задачи — отличить ссылку на КОНКРЕТНЫЙ материал от ссылки «на
всё подряд». Подпись «Подписаться: t.me/channel» или ссылка на главную страницу
сайта есть почти в каждом посте канала; если считать её признаком дубля, все
посты канала после первого были бы отброшены как повторы. Такие ссылки
канонизация отбрасывает (возвращает None).
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit

from news_aggregator.core.models import ProcessedMessage

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "yclid",
        "ysclid",
        "dclid",
        "msclkid",
        "gbraid",
        "wbraid",
        "_openstat",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref_src",
        "ref_url",
        "spm",
        "cmpid",
    }
)
_TELEGRAM_HOSTS = frozenset({"t.me", "telegram.me", "telegram.dog"})
_DEFAULT_PORTS = {80, 443}


def _canonical_telegram(segments: list[str]) -> str | None:
    """Ссылка на пост Telegram: t.me/<канал>/<номер> или t.me/c/<id>/<номер>."""
    if segments and segments[0] == "s":  # веб-превью: t.me/s/<канал>/<номер>
        segments = segments[1:]
    if (
        len(segments) >= 3
        and segments[0] == "c"
        and segments[1].isdigit()
        and segments[2].isdigit()
    ):
        return f"t.me/c/{segments[1]}/{segments[2]}"
    if len(segments) >= 2 and segments[1].isdigit():
        return f"t.me/{segments[0].lower()}/{segments[1]}"
    return None  # профиль канала, приглашение, бот — не материал


def canonicalize_url(url: str) -> str | None:
    """Каноническая форма ссылки или None, если это не ссылка на конкретный материал."""
    text = url.strip()
    if not text:
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not host:
        return None

    host = host.lower().rstrip(".")
    host = host.removeprefix("www.")
    segments = [segment for segment in parts.path.split("/") if segment]

    if host in _TELEGRAM_HOSTS:
        return _canonical_telegram(segments)
    if not segments:
        return None  # главная страница сайта — не материал

    query = sorted(
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if not name.lower().startswith(_TRACKING_PREFIXES) and name.lower() not in _TRACKING_PARAMS
    )
    netloc = f"{host}:{port}" if port and port not in _DEFAULT_PORTS else host
    canonical = f"{netloc}/{'/'.join(segments)}"
    return f"{canonical}?{urlencode(query)}" if query else canonical


def entity_urls(message: ProcessedMessage) -> list[str]:
    """URL гиперссылок на словах («Подробнее»), которых нет в самом тексте.

    Ядро не знает про Telegram, поэтому у каждого объекта форматирования
    (у Telethon — MessageEntityTextUrl) берётся строковый атрибут `url`, если он есть.
    """
    urls: list[str] = []
    for entity in message.raw.formatting_entities:
        url = getattr(entity, "url", None)
        if isinstance(url, str) and url:
            urls.append(url)
    return urls


def canonical_content_links(links: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """Пары (исходная ссылка, каноническая) только для ссылок на конкретный материал.

    Порядок сохраняется, повторы по канонической форме убираются.
    """
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in links:
        canonical = canonicalize_url(raw)
        if canonical is not None and canonical not in seen:
            seen.add(canonical)
            result.append((raw, canonical))
    return tuple(result)
