from datetime import datetime
from typing import Any

import pytest

from news_aggregator.core.models import MediaAttachment, ProcessedMessage, RawMessage
from news_aggregator.publishers.telegram_publisher import TelegramPublisher


class _FakeTelethonClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.sent_files: list[dict[str, Any]] = []

    async def send_message(
        self,
        target: str,
        text: str,
        link_preview: bool = True,
        parse_mode: object = (),
        formatting_entities: list[object] | None = None,
    ) -> None:
        self.sent.append(
            {
                "target": target,
                "text": text,
                "link_preview": link_preview,
                "parse_mode": parse_mode,
                "formatting_entities": formatting_entities,
            }
        )

    async def send_file(
        self,
        target: str,
        file: object,
        caption: str = "",
        parse_mode: object = (),
        formatting_entities: list[object] | None = None,
    ) -> None:
        self.sent_files.append(
            {
                "target": target,
                "file": file,
                "caption": caption,
                "parse_mode": parse_mode,
                "formatting_entities": formatting_entities,
            }
        )


def _make_message(
    text: str,
    links: tuple[str, ...] = (),
    media: MediaAttachment | None = None,
    formatting_entities: tuple[object, ...] = (),
    source_display_name: str = "",
) -> ProcessedMessage:
    raw = RawMessage(
        source_id="src1",
        external_id="1",
        text=text,
        posted_at=datetime.now(),
        fetched_at=datetime.now(),
        media=media,
        formatting_entities=formatting_entities,
        source_display_name=source_display_name,
    )
    return ProcessedMessage(
        raw=raw, normalized_text=text.lower(), text_hash="deadbeef", links=links
    )


async def test_publish_sends_stripped_text_to_target_channel() -> None:
    client = _FakeTelethonClient()
    publisher = TelegramPublisher(client, target_channel="@target")

    await publisher.publish(_make_message("  Текст новости  "))

    assert len(client.sent) == 1
    sent = client.sent[0]
    assert sent["target"] == "@target"
    assert sent["text"] == "Текст новости"
    assert sent["link_preview"] is False
    assert sent["parse_mode"] is None
    assert sent["formatting_entities"] is None


async def test_publish_enables_link_preview_when_links_present() -> None:
    client = _FakeTelethonClient()
    publisher = TelegramPublisher(client, target_channel="@target")

    await publisher.publish(_make_message("Новость", links=("https://example.com",)))

    assert client.sent[0]["link_preview"] is True


async def test_publish_with_media_sends_file_with_caption_instead_of_text_message() -> None:
    client = _FakeTelethonClient()
    publisher = TelegramPublisher(client, target_channel="@target")
    native_ref = object()
    media = MediaAttachment(kind="photo", native_ref=native_ref)

    await publisher.publish(_make_message("Подпись к фото", media=media))

    assert client.sent == []
    assert len(client.sent_files) == 1
    sent_file = client.sent_files[0]
    assert sent_file["target"] == "@target"
    assert sent_file["file"] is native_ref
    assert sent_file["caption"] == "Подпись к фото"


async def test_publish_appends_source_channel_footer() -> None:
    client = _FakeTelethonClient()
    publisher = TelegramPublisher(client, target_channel="@target")

    await publisher.publish(_make_message("Текст новости", source_display_name="Мой любимый канал"))

    assert client.sent[0]["text"] == "Текст новости\n\nИсточник: Мой любимый канал"


async def test_publish_without_display_name_has_no_footer() -> None:
    client = _FakeTelethonClient()
    publisher = TelegramPublisher(client, target_channel="@target")

    await publisher.publish(_make_message("Текст новости", source_display_name=""))

    assert client.sent[0]["text"] == "Текст новости"


async def test_publish_passes_through_formatting_entities_unmodified() -> None:
    """Гиперссылки (MessageEntityTextUrl и т.п.) должны уйти в Telegram как
    есть, а не потеряться — иначе текст без ссылки на слове теряет смысл."""
    client = _FakeTelethonClient()
    publisher = TelegramPublisher(client, target_channel="@target")
    fake_entity = object()

    await publisher.publish(_make_message("Подробнее", formatting_entities=(fake_entity,)))

    assert client.sent[0]["formatting_entities"] == [fake_entity]
    assert client.sent[0]["parse_mode"] is None


async def test_publish_with_entities_does_not_strip_text_to_preserve_offsets() -> None:
    """Обрезка пробелов сдвинула бы offset у entities (они считаются в
    UTF-16 code units от начала исходного текста) — с entities текст не
    трогаем, даже если по краям есть пробелы."""
    client = _FakeTelethonClient()
    publisher = TelegramPublisher(client, target_channel="@target")
    fake_entity = object()

    await publisher.publish(
        _make_message("  Текст со ссылкой  ", formatting_entities=(fake_entity,))
    )

    assert client.sent[0]["text"] == "  Текст со ссылкой  "


async def test_publish_with_media_also_appends_footer_and_entities() -> None:
    client = _FakeTelethonClient()
    publisher = TelegramPublisher(client, target_channel="@target")
    native_ref = object()
    fake_entity = object()
    media = MediaAttachment(kind="photo", native_ref=native_ref)

    await publisher.publish(
        _make_message(
            "Подпись",
            media=media,
            formatting_entities=(fake_entity,),
            source_display_name="Канал X",
        )
    )

    sent_file = client.sent_files[0]
    assert sent_file["caption"] == "Подпись\n\nИсточник: Канал X"
    assert sent_file["formatting_entities"] == [fake_entity]
    assert sent_file["parse_mode"] is None


def test_empty_target_channel_raises() -> None:
    with pytest.raises(ValueError):
        TelegramPublisher(_FakeTelethonClient(), target_channel="")
