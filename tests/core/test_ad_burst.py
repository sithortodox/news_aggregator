"""Реклама несколькими сообщениями: маркировка или ссылка только в одном.

Регрессия: concertzaal выложил рекламу Точка Банка двумя сообщениями в одну
секунду (19571 «Чёрная пятница в Точка Банк…» без ссылки и 19573 со ссылкой
utm_medium=seeding). utm_filter отсеивал бы только второе, а первое выходило.
"""

from datetime import datetime, timedelta

from news_aggregator.core.models import ProcessedMessage, RawMessage, Source, SourceKind
from news_aggregator.core.pipeline import AggregatorPipeline
from news_aggregator.filters.length_filter import LengthFilter
from news_aggregator.filters.utm_filter import UtmSourceFilter
from tests.core.test_pipeline import _RecordingPublisher, _ScriptedReader
from tests.support.in_memory_source_repository import InMemorySourceRepository
from tests.support.in_memory_storage import InMemoryStorage

_T0 = datetime(2026, 11, 13, 15, 56, 6)
_LEAD = "Чёрная пятница в Точка Банк: тариф «Можно всё» со скидкой 70%"
_AD = (
    "Успейте открыть счёт для бизнеса в Точка Банке до 30 ноября по ссылке "
    "https://bank-tochka.ru/bf/?utm_source_tg&utm_medium=seeding&utm_campaign=bf26_rko"
)
_NEWS = "Telegram отключил HTML-виджеты в чатах из-за уязвимостей, найденных исследователями"


def _raw(external_id: str, text: str, seconds: float = 0) -> RawMessage:
    at = _T0 + timedelta(seconds=seconds)
    return RawMessage(
        source_id="concertzaal", external_id=external_id, text=text, posted_at=at, fetched_at=at
    )


async def _run(messages: list[RawMessage], ad_burst_seconds: int = 10) -> list[str]:
    publisher = _RecordingPublisher()
    pipeline = AggregatorPipeline(
        source_repository=InMemorySourceRepository(
            [
                Source(
                    id="concertzaal",
                    kind=SourceKind.TELEGRAM_CHANNEL,
                    identifier="x",
                    display_name="concertzaal",
                )
            ]
        ),
        readers=[_ScriptedReader("concertzaal", messages)],
        filters=[
            LengthFilter(min_length=5),
            UtmSourceFilter(["utm_source", "utm_medium", "utm_campaign"]),
        ],
        deduplicators=[],
        enrichers=[],
        publishers=[publisher],  # type: ignore[list-item]
        storage=InMemoryStorage(),
        dry_run=False,
        ad_burst_seconds=ad_burst_seconds,
    )
    stats = await pipeline.run_once()
    assert stats.messages_fetched == len(messages)
    published: list[ProcessedMessage] = publisher.published
    return [m.raw.external_id for m in published]


async def test_lead_message_of_an_ad_is_dropped_too() -> None:
    assert await _run([_raw("19571", _LEAD), _raw("19573", _AD)]) == []


async def test_without_the_rule_only_the_marked_message_is_dropped() -> None:
    assert await _run([_raw("19571", _LEAD), _raw("19573", _AD)], ad_burst_seconds=0) == ["19571"]


async def test_messages_further_apart_are_independent() -> None:
    published = await _run([_raw("19571", _LEAD), _raw("19573", _AD, seconds=60)])

    assert published == ["19571"]


async def test_long_post_next_to_an_ad_is_kept() -> None:
    long_news = _NEWS + ". " + "Подробности разбора уязвимостей и реакции команды. " * 8

    assert await _run([_raw("19571", long_news), _raw("19573", _AD)]) == ["19571"]


async def test_burst_without_ads_is_published_whole() -> None:
    published = await _run([_raw("1", _LEAD), _raw("2", _NEWS, seconds=3)])

    assert published == ["1", "2"]


async def test_cursor_moves_past_the_whole_burst() -> None:
    storage = InMemoryStorage()
    pipeline = AggregatorPipeline(
        source_repository=InMemorySourceRepository(
            [
                Source(
                    id="concertzaal",
                    kind=SourceKind.TELEGRAM_CHANNEL,
                    identifier="x",
                    display_name="concertzaal",
                )
            ]
        ),
        readers=[_ScriptedReader("concertzaal", [_raw("19571", _LEAD), _raw("19573", _AD)])],
        filters=[UtmSourceFilter(["utm_medium"])],
        deduplicators=[],
        enrichers=[],
        publishers=[_RecordingPublisher()],  # type: ignore[list-item]
        storage=storage,
        dry_run=False,
        ad_burst_seconds=10,
    )

    await pipeline.run_once()

    assert await storage.get_last_external_id("concertzaal") == "19573"
