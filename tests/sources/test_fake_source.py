from news_aggregator.core.models import Source, SourceKind
from news_aggregator.sources.fake_source import FakeSourceReader


def _source() -> Source:
    return Source(id="demo", kind=SourceKind.FAKE, identifier="demo", display_name="Demo")


def test_supports_only_fake_sources() -> None:
    reader = FakeSourceReader()
    assert reader.supports(_source()) is True
    telegram_source = Source(
        id="t", kind=SourceKind.TELEGRAM_CHANNEL, identifier="@x", display_name="T"
    )
    assert reader.supports(telegram_source) is False


async def test_reads_all_messages_from_scratch() -> None:
    reader = FakeSourceReader()
    messages = [msg async for msg in reader.read_new_messages(_source(), None)]
    assert len(messages) > 0
    assert all(msg.source_id == "demo" for msg in messages)


async def test_reads_only_new_messages_since_cursor() -> None:
    reader = FakeSourceReader()
    all_messages = [msg async for msg in reader.read_new_messages(_source(), None)]
    total = len(all_messages)

    since_second = all_messages[1].external_id
    remaining = [msg async for msg in reader.read_new_messages(_source(), since_second)]

    assert len(remaining) == total - 2


async def test_contains_intentional_near_duplicate_for_dedup_demo() -> None:
    reader = FakeSourceReader()
    messages = [msg async for msg in reader.read_new_messages(_source(), None)]
    texts = [msg.text for msg in messages]
    assert any("ставку" in t for t in texts)  # намеренный почти-дубль для демонстрации
