from datetime import datetime
from typing import Any

import pytest
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageEntityTextUrl

from news_aggregator.core.errors import (
    PartialPublishError,
    PublishUnavailableError,
    RateLimitedError,
)
from news_aggregator.core.models import MediaAttachment, ProcessedMessage, RawMessage
from news_aggregator.publishers.telegram_publisher import TelegramPublisher


class _FakeTelethonClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.sent_files: list[dict[str, Any]] = []
        self.connected = True
        self.connect_calls = 0

    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connect_calls += 1
        self.connected = True

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


# ------------------------------------------------------------------ FloodWait


async def test_floodwait_on_send_message_becomes_rate_limited_error() -> None:
    from telethon.errors import FloodWaitError

    from news_aggregator.core.errors import RateLimitedError

    class _Flooding(_FakeTelethonClient):
        async def send_message(self, *args: Any, **kwargs: Any) -> None:
            raise FloodWaitError(request=None, capture=180)

    publisher = TelegramPublisher(_Flooding(), target_channel="@target")  # type: ignore[arg-type]

    with pytest.raises(RateLimitedError) as info:
        await publisher.publish(_make_message("Текст новости"))

    assert info.value.retry_after_seconds == 180
    assert "@target" in str(info.value)


async def test_floodwait_on_send_file_becomes_rate_limited_error() -> None:
    from telethon.errors import FloodWaitError

    from news_aggregator.core.errors import RateLimitedError

    class _Flooding(_FakeTelethonClient):
        async def send_file(self, *args: Any, **kwargs: Any) -> None:
            raise FloodWaitError(request=None, capture=45)

    publisher = TelegramPublisher(_Flooding(), target_channel="@target")  # type: ignore[arg-type]
    media = MediaAttachment(kind="photo", native_ref=object())

    with pytest.raises(RateLimitedError) as info:
        await publisher.publish(_make_message("С фото", media=media))

    assert info.value.retry_after_seconds == 45


async def test_other_publish_errors_are_not_converted() -> None:
    class _Broken(_FakeTelethonClient):
        async def send_message(self, *args: Any, **kwargs: Any) -> None:
            raise ValueError("chat not found")

    publisher = TelegramPublisher(_Broken(), target_channel="@target")  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        await publisher.publish(_make_message("Текст"))


# ------------------------------------------------------- недоступность канала


def _unavailable_errors() -> list[Exception]:
    from telethon import errors

    return [
        ConnectionError("Cannot send requests while disconnected"),
        TimeoutError(),
        errors.ServerError(None, "INTERNAL"),
        errors.TimedOutError(None, "Timeout"),
        errors.AuthKeyUnregisteredError(request=None),
        errors.ChannelPrivateError(request=None),
        errors.ChatAdminRequiredError(request=None),
        errors.ChatWriteForbiddenError(request=None),
        errors.UserBannedInChannelError(request=None),
    ]


@pytest.mark.parametrize("error", _unavailable_errors(), ids=lambda e: type(e).__name__)
async def test_target_level_errors_become_publish_unavailable(error: Exception) -> None:
    class _Down(_FakeTelethonClient):
        async def send_message(self, *args: Any, **kwargs: Any) -> None:
            raise error

    publisher = TelegramPublisher(_Down(), target_channel="@target")  # type: ignore[arg-type]

    with pytest.raises(PublishUnavailableError, match="@target"):
        await publisher.publish(_make_message("Текст"))


async def test_unavailable_on_send_file_is_converted_too() -> None:
    class _Down(_FakeTelethonClient):
        async def send_file(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("disconnected")

    message = _make_message("Текст", media=MediaAttachment(kind="photo", native_ref=object()))
    publisher = TelegramPublisher(_Down(), target_channel="@target")  # type: ignore[arg-type]

    with pytest.raises(PublishUnavailableError):
        await publisher.publish(message)


async def test_reconnects_before_publishing_when_telethon_gave_up() -> None:
    client = _FakeTelethonClient()
    client.connected = False
    publisher = TelegramPublisher(client, target_channel="@target")  # type: ignore[arg-type]

    await publisher.publish(_make_message("Текст"))

    assert client.connect_calls == 1
    assert len(client.sent) == 1


async def test_failed_reconnect_means_publisher_is_unavailable() -> None:
    class _NoNetwork(_FakeTelethonClient):
        async def connect(self) -> None:
            raise ConnectionError("нет сети")

    client = _NoNetwork()
    client.connected = False
    publisher = TelegramPublisher(client, target_channel="@target")  # type: ignore[arg-type]

    with pytest.raises(PublishUnavailableError):
        await publisher.publish(_make_message("Текст"))


def _message_level_errors() -> list[Exception]:
    from telethon import errors

    return [
        errors.MessageTooLongError(request=None),
        errors.MediaCaptionTooLongError(request=None),
        errors.ChatSendMediaForbiddenError(request=None),
    ]


@pytest.mark.parametrize("error", _message_level_errors(), ids=lambda e: type(e).__name__)
async def test_message_level_errors_are_not_retried(error: Exception) -> None:
    class _Rejecting(_FakeTelethonClient):
        async def send_message(self, *args: Any, **kwargs: Any) -> None:
            raise error

    publisher = TelegramPublisher(_Rejecting(), target_channel="@target")  # type: ignore[arg-type]

    with pytest.raises(type(error)):
        await publisher.publish(_make_message("Текст"))


# ------------------------------------------------------------- длинные посты

_FOOTER = "\n\nИсточник: Канал"
_PHOTO = MediaAttachment(kind="photo", native_ref="photo")


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _words(length: int) -> str:
    return ("слово " * (length // 6 + 1))[: length - 1] + "."


async def _publish(text: str, **kwargs: Any) -> _FakeTelethonClient:
    client = _FakeTelethonClient()
    publisher = TelegramPublisher(client, target_channel="@target")  # type: ignore[arg-type]
    await publisher.publish(_make_message(text, source_display_name="Канал", **kwargs))
    return client


async def test_text_exactly_at_the_limit_stays_one_message() -> None:
    text = _words(4096 - len(_FOOTER))

    client = await _publish(text)

    assert [m["text"] for m in client.sent] == [text + _FOOTER]


async def test_long_text_is_split_without_losing_anything() -> None:
    text = _words(10000)

    client = await _publish(text)

    assert len(client.sent) == 3
    assert all(_utf16(m["text"]) <= 4096 for m in client.sent)
    assert "".join(m["text"] for m in client.sent) == text + _FOOTER


async def test_footer_is_not_torn_off_into_its_own_message() -> None:
    client = await _publish(_words(4090))

    assert len(client.sent) == 2
    assert client.sent[-1]["text"].endswith(_FOOTER)
    assert client.sent[-1]["text"] != _FOOTER.lstrip()
    assert _FOOTER not in client.sent[0]["text"]


async def test_length_is_counted_in_utf16_units() -> None:
    client = await _publish("😀 " * 1500)  # 3000 символов, но 4500 единиц UTF-16

    assert len(client.sent) == 2
    assert all(_utf16(m["text"]) <= 4096 for m in client.sent)


async def test_hyperlink_in_the_tail_keeps_pointing_at_its_word() -> None:
    head = _words(5000)
    text = f"{head} Подробнее"
    url = "https://example.com/a"
    entity = MessageEntityTextUrl(offset=len(head) + 1, length=len("Подробнее"), url=url)

    client = await _publish(text, formatting_entities=(entity,))

    assert client.sent[0]["formatting_entities"] is None
    last = client.sent[-1]
    (moved,) = last["formatting_entities"]
    assert moved.url == url
    assert last["text"][moved.offset : moved.offset + moved.length] == "Подробнее"


async def test_link_preview_only_on_the_first_part() -> None:
    client = await _publish(_words(5000), links=("https://example.com/a",))

    assert [m["link_preview"] for m in client.sent] == [True, False]


async def test_caption_exactly_at_the_limit_stays_a_caption() -> None:
    text = _words(1024 - len(_FOOTER))

    client = await _publish(text, media=_PHOTO)

    assert [f["caption"] for f in client.sent_files] == [text + _FOOTER]
    assert client.sent == []


async def test_text_over_the_caption_limit_follows_the_media() -> None:
    text = _words(1500)
    entity = MessageEntityTextUrl(offset=0, length=5, url="https://example.com/a")

    client = await _publish(text, media=_PHOTO, formatting_entities=(entity,))

    assert client.sent_files[0]["caption"] == ""
    assert client.sent_files[0]["formatting_entities"] is None
    assert [m["text"] for m in client.sent] == [text + _FOOTER]
    assert client.sent[0]["formatting_entities"] == [entity]
    assert client.sent[0]["link_preview"] is False


async def test_media_with_text_over_the_message_limit() -> None:
    text = _words(6000)

    client = await _publish(text, media=_PHOTO)

    assert len(client.sent_files) == 1
    assert "".join(m["text"] for m in client.sent) == text + _FOOTER
    assert len(client.sent) == 2


class _FailsOnSend(_FakeTelethonClient):
    """Падает на send_message с заданным порядковым номером (с единицы)."""

    def __init__(self, failing_call: int, error: Exception) -> None:
        super().__init__()
        self._failing_call = failing_call
        self._error = error
        self._calls = 0

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        self._calls += 1
        if self._calls == self._failing_call:
            raise self._error
        await super().send_message(*args, **kwargs)


def _flood() -> FloodWaitError:
    return FloodWaitError(request=None, capture=120)


@pytest.mark.parametrize("error", [_flood(), ConnectionError("обрыв")], ids=["flood", "network"])
async def test_failure_after_the_first_part_is_not_retried(error: Exception) -> None:
    """Повтор продублировал бы уже вышедшее начало поста."""
    client = _FailsOnSend(2, error)
    publisher = TelegramPublisher(client, target_channel="@target")  # type: ignore[arg-type]

    with pytest.raises(PartialPublishError, match="не полностью"):
        await publisher.publish(_make_message(_words(5000)))

    assert len(client.sent) == 1


async def test_failure_on_the_first_part_is_still_retried() -> None:
    client = _FailsOnSend(1, _flood())
    publisher = TelegramPublisher(client, target_channel="@target")  # type: ignore[arg-type]

    with pytest.raises(RateLimitedError):
        await publisher.publish(_make_message(_words(5000)))


async def test_failure_of_text_after_media_is_not_retried() -> None:
    client = _FailsOnSend(1, ConnectionError("обрыв"))
    publisher = TelegramPublisher(client, target_channel="@target")  # type: ignore[arg-type]

    with pytest.raises(PartialPublishError):
        await publisher.publish(_make_message(_words(2000), media=_PHOTO))
