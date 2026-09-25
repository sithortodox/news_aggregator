"""Тесты TelegramSourceReader.

Используется лёгкий тестовый двойник клиента с интерфейсом, совместимым с
Telethon (get_entity/iter_messages), а не настоящий Telethon и не реальный
Telegram — согласно требованию "не использовать реальный Telegram в
юнит-тестах".
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from news_aggregator.core.models import Source, SourceKind
from news_aggregator.sources.telegram_source import TelegramSourceReader


class _FakeTgMessage:
    def __init__(
        self,
        id: int,
        message: str | None,
        date: datetime,
        media: object | None = None,
        photo: object | None = None,
    ) -> None:
        self.id = id
        self.message = message
        self.date = date
        self.media = media
        self.photo = photo


class _FakeTelethonClient:
    """Двойник telethon.TelegramClient с минимально нужным интерфейсом."""

    def __init__(self, messages: list[_FakeTgMessage]) -> None:
        self._messages = messages
        self.get_entity_calls: list[str] = []
        self.iter_calls: list[tuple[int, bool]] = []

    async def get_entity(self, identifier: str) -> object:
        self.get_entity_calls.append(identifier)
        return SimpleNamespace(name=identifier)

    async def iter_messages(self, entity: object, min_id: int = 0, reverse: bool = True):
        self.iter_calls.append((min_id, reverse))
        for msg in self._messages:
            if msg.id > min_id:
                yield msg


def _tg_source() -> Source:
    return Source(id="ch1", kind=SourceKind.TELEGRAM_CHANNEL, identifier="@ch1", display_name="Ch1")


def test_supports_telegram_channel_and_group_but_not_fake() -> None:
    reader = TelegramSourceReader(_FakeTelethonClient([]))
    assert reader.supports(_tg_source()) is True
    group_source = Source(
        id="g1", kind=SourceKind.TELEGRAM_GROUP, identifier="@g1", display_name="G1"
    )
    assert reader.supports(group_source) is True
    fake_source = Source(id="f1", kind=SourceKind.FAKE, identifier="f1", display_name="F1")
    assert reader.supports(fake_source) is False


async def test_reads_all_messages_from_scratch_skipping_empty_text() -> None:
    now = datetime.now(tz=UTC)
    client = _FakeTelethonClient(
        [
            _FakeTgMessage(1, "Первое сообщение с достаточным текстом", now),
            _FakeTgMessage(2, "", now),  # без текста - должно быть пропущено
            _FakeTgMessage(3, "Третье сообщение с текстом", now),
        ]
    )
    reader = TelegramSourceReader(client)

    results = [r async for r in reader.read_new_messages(_tg_source(), None)]

    assert [r.external_id for r in results] == ["1", "3"]
    assert client.get_entity_calls == ["@ch1"]
    assert client.iter_calls == [(0, True)]


async def test_captures_photo_media_as_attachment() -> None:
    now = datetime.now(tz=UTC)
    fake_photo_media = SimpleNamespace(id="photo-media-ref")
    client = _FakeTelethonClient(
        [
            _FakeTgMessage(
                1, "Подпись к фото", now, media=fake_photo_media, photo=SimpleNamespace()
            ),
        ]
    )
    reader = TelegramSourceReader(client)

    results = [r async for r in reader.read_new_messages(_tg_source(), None)]

    assert len(results) == 1
    assert results[0].media is not None
    assert results[0].media.kind == "photo"
    assert results[0].media.native_ref is fake_photo_media


async def test_captures_document_media_as_attachment() -> None:
    now = datetime.now(tz=UTC)
    fake_doc_media = SimpleNamespace(id="doc-media-ref")
    client = _FakeTelethonClient(
        [_FakeTgMessage(1, "Подпись к файлу", now, media=fake_doc_media, photo=None)]
    )
    reader = TelegramSourceReader(client)

    results = [r async for r in reader.read_new_messages(_tg_source(), None)]

    assert results[0].media is not None
    assert results[0].media.kind == "document"


async def test_no_media_leaves_media_field_none() -> None:
    now = datetime.now(tz=UTC)
    client = _FakeTelethonClient([_FakeTgMessage(1, "Просто текст", now)])
    reader = TelegramSourceReader(client)

    results = [r async for r in reader.read_new_messages(_tg_source(), None)]

    assert results[0].media is None


async def test_reads_only_messages_after_cursor() -> None:
    now = datetime.now(tz=UTC)
    client = _FakeTelethonClient(
        [
            _FakeTgMessage(1, "Первое", now),
            _FakeTgMessage(2, "Второе", now),
            _FakeTgMessage(3, "Третье", now),
        ]
    )
    reader = TelegramSourceReader(client)

    results = [r async for r in reader.read_new_messages(_tg_source(), "1")]

    assert [r.external_id for r in results] == ["2", "3"]
    assert client.iter_calls == [(1, True)]
