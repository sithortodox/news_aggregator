from datetime import datetime

import pytest

from news_aggregator.core.models import ProcessedMessage, RawMessage
from news_aggregator.publishers.telegram_publisher import TelegramPublisher


class _FakeTelethonClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, bool]] = []

    async def send_message(self, target: str, text: str, link_preview: bool = True) -> None:
        self.sent.append((target, text, link_preview))


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


async def test_publish_sends_stripped_text_to_target_channel() -> None:
    client = _FakeTelethonClient()
    publisher = TelegramPublisher(client, target_channel="@target")

    await publisher.publish(_make_message("  Текст новости  "))

    assert client.sent == [("@target", "Текст новости", False)]


async def test_publish_enables_link_preview_when_links_present() -> None:
    client = _FakeTelethonClient()
    publisher = TelegramPublisher(client, target_channel="@target")

    await publisher.publish(_make_message("Новость", links=("https://example.com",)))

    assert client.sent == [("@target", "Новость", True)]


def test_empty_target_channel_raises() -> None:
    with pytest.raises(ValueError):
        TelegramPublisher(_FakeTelethonClient(), target_channel="")
