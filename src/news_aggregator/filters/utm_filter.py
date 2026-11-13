"""Фильтр, отсеивающий посты со ссылками, в которых есть utm-метка источника.

Партнёрские и рекламные ссылки почти всегда несут `utm_source=...`: так
рекламодатель отслеживает, откуда пришёл переход. Пост с такой ссылкой
считается рекламным и не публикуется.

Проверяются ОБА источника ссылок:

* сам текст поста. Ищется именно метка в тексте, а не только в ссылках, которые
  извлёк extract_links: он знает лишь `http(s)://`, `www.` и `t.me/`, а Telegram
  делает кликабельной и голую `shop.example.com/sale?utm_source=tg`;
* ссылки-гиперссылки на слове («Подробнее»): они лежат не в тексте, а в
  formatting_entities (у Telethon это MessageEntityTextUrl.url). Ядро не знает
  про Telegram, поэтому у каждого объекта форматирования просто берётся
  строковый атрибут `url`, если он есть.

Метка ищется как параметр запроса или фрагмента и во вложенных ссылках-
редиректах (`?url=https%3A%2F%2Fsite.ru%2F%3Futm_source%3Dtg`): ссылка
раскодируется, а совпадение требует разделителя перед именем параметра, так что
`xutm_source=1` и путь вида `/utm_source=a` не считаются.

Предупреждение: многие НОВОСТНЫЕ сайты тоже добавляют `utm_source` к ссылкам,
которыми делятся. Такие посты будут отброшены вместе с рекламой. Список
проверяемых параметров настраивается (param_names), причина отсева видна в логе.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import unquote, urlsplit

from news_aggregator.core.interfaces import IFilter
from news_aggregator.core.models import ProcessedMessage
from news_aggregator.filters._text_sources import searchable_texts

_DEFAULT_PARAMS: tuple[str, ...] = ("utm_source",)


def _host(url: str) -> str:
    text = url if "://" in url else "https://" + url
    try:
        return urlsplit(text).hostname or url[:40]
    except ValueError:
        return url[:40]


def _token_around(text: str, position: int) -> str:
    """Слово (без пробелов), внутри которого находится позиция, — то есть сама ссылка."""
    start = position
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = position
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[start:end].strip("()[]<>\"'«».,;:!?")


class UtmSourceFilter(IFilter):
    """Отбрасывает сообщения со ссылкой, содержащей один из utm-параметров."""

    def __init__(self, param_names: Sequence[str] | None = None) -> None:
        names = param_names if param_names is not None else _DEFAULT_PARAMS
        cleaned = [name.strip().lower() for name in names if name and name.strip()]
        if not cleaned:
            raise ValueError("param_names не должен быть пустым")
        self._names = tuple(dict.fromkeys(cleaned))
        # Имя параметра обязано идти сразу после разделителя (? & # ;), иначе
        # «xutm_source=1» или кусок пути дали бы ложное срабатывание.
        self._pattern = re.compile(
            r"[?&#;](" + "|".join(re.escape(name) for name in self._names) + r")=", re.IGNORECASE
        )

    @property
    def name(self) -> str:
        return "utm_filter"

    @property
    def detects_ads(self) -> bool:
        return True

    def should_drop(self, message: ProcessedMessage) -> tuple[bool, str | None]:
        for segment in searchable_texts(message):
            decoded = unquote(segment)
            match = self._pattern.search(decoded)
            if match:
                return (
                    True,
                    f"рекламная ссылка: параметр {match.group(1).lower()} "
                    f"в ссылке на {_host(_token_around(decoded, match.start()))}",
                )
        return False, None
