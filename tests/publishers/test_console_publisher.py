import logging
from datetime import datetime

from news_aggregator.core.models import ProcessedMessage, RawMessage
from news_aggregator.publishers.console_publisher import ConsolePublisher


def _make_message(text: str, links: tuple[str, ...] = ()) -> ProcessedMessage:
    raw = RawMessage(
        source_id="src1",
        external_id="1",
        text=text,
        posted_at=datetime.now(),
        fetched_at=datetime.now(),
    )
    return ProcessedMessage(
        raw=raw, normalized_text=text.lower(), text_hash="deadbeef", links=links
    )


async def test_publish_logs_message_with_prefix(caplog) -> None:  # type: ignore[no-untyped-def]
    publisher = ConsolePublisher(prefix="[TEST]")
    with caplog.at_level(logging.INFO, logger="news_aggregator.publish"):
        await publisher.publish(_make_message("Важная новость"))

    assert any("[TEST]" in record.message for record in caplog.records)
    assert any("Важная новость" in record.message for record in caplog.records)


async def test_publish_includes_links_when_present(caplog) -> None:  # type: ignore[no-untyped-def]
    publisher = ConsolePublisher()
    with caplog.at_level(logging.INFO, logger="news_aggregator.publish"):
        await publisher.publish(
            _make_message("Новость со ссылкой", links=("https://example.com/x",))
        )

    assert any("https://example.com/x" in record.message for record in caplog.records)


async def test_publish_does_not_raise_without_links() -> None:
    publisher = ConsolePublisher()
    await publisher.publish(_make_message("Новость без ссылок"))  # не должно упасть
