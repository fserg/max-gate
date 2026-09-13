from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Message

from maxgate.domain import Attachment, Entity, RelayMessage
from maxgate.tg.bot import TOPIC_COLORS, TgBot
from maxgate.tg.media import DOWNLOAD_LIMIT, MediaTooLarge, download, send
from maxgate.tg.messages import from_telegram


def message(**kwargs):
    data = dict(
        message_id=1,
        date=datetime.now(UTC),
        chat={"id": 10, "type": "private"},
        from_user={"id": 1, "is_bot": False, "first_name": "Owner"},
        message_thread_id=2,
    )
    data.update(kwargs)
    return Message(**data)


async def test_owner_checked_by_from_id(storage):
    _, sessions, _ = storage
    fake = SimpleNamespace(send_message=AsyncMock())
    adapter = TgBot("", SimpleNamespace(owner_tg_user_id=1), sessions, bot=fake)
    handler = AsyncMock()
    foreign = message(from_user={"id": 10, "is_bot": False, "first_name": "Other"}, text="hi")
    await adapter._owner_only(handler, foreign, {})
    handler.assert_not_called()
    await adapter._owner_only(handler, message(text="hi"), {})
    handler.assert_awaited_once()
    await adapter._owner_only(handler, foreign.model_copy(update={"text": "/start"}), {})
    fake.send_message.assert_awaited_once()


async def test_inbox_binding_and_topic_colors(storage):
    from maxgate.db.models import Account

    _, sessions, _ = storage
    async with sessions() as session:
        account = await session.get(Account, 1)
    fake = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(has_topics_enabled=True)),
        send_message=AsyncMock(),
        create_forum_topic=AsyncMock(return_value=SimpleNamespace(message_thread_id=5)),
        edit_forum_topic=AsyncMock(),
    )
    adapter = TgBot("", account, sessions, bot=fake)
    await adapter._start(message(text="/start"))
    async with sessions() as session:
        assert (await session.get(Account, 1)).inbox_chat_id == 10
    for kind, color in TOPIC_COLORS.items():
        assert await adapter.create_topic("Title", kind) == 5
        assert fake.create_forum_topic.call_args.kwargs["icon_color"] == color
    await adapter.edit_topic(5, "New")
    assert adapter._pending_titles[5] == "New"


async def test_long_caption_after_album(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"fake image")
    bot = SimpleNamespace(
        send_media_group=AsyncMock(return_value=[1, 2]), send_message=AsyncMock(return_value=3)
    )
    result = await send(
        bot,
        10,
        2,
        RelayMessage(
            "a" * 1025,
            [Entity("bold", 0, 1025)],
            attachments=[Attachment("photo", path), Attachment("photo", path)],
        ),
    )
    assert result == [1, 2, 3]
    kwargs = bot.send_media_group.call_args.kwargs
    assert kwargs["message_thread_id"] == 2
    assert all(item.caption is None for item in kwargs["media"])
    assert bot.send_message.call_args.kwargs["text"] == "a" * 1025


async def test_album_family_and_batch_limits(tmp_path):
    path = tmp_path / "media"
    path.write_bytes(b"data")
    bot = SimpleNamespace(
        send_media_group=AsyncMock(return_value=[1]), send_document=AsyncMock(return_value=2)
    )
    attachments = [Attachment("photo", path)] * 12 + [Attachment("document", path)]
    await send(bot, 10, 2, RelayMessage(attachments=attachments))
    assert [len(c.kwargs["media"]) for c in bot.send_media_group.call_args_list] == [10, 2]
    bot.send_document.assert_awaited_once()


async def test_voice_sniff_and_duration(tmp_path):
    path = tmp_path / "voice"
    path.write_bytes(b"OggS123")
    bot = SimpleNamespace(send_voice=AsyncMock())
    await send(bot, 10, 2, RelayMessage(attachments=[Attachment("voice", path, duration=2345)]))
    assert bot.send_voice.call_args.kwargs["duration"] == 2


async def test_download_limit_and_partial_cleanup(tmp_path):
    bot = SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_path="file", file_size=None)),
        download_file=AsyncMock(),
    )
    with pytest.raises(MediaTooLarge):
        await download(bot, Attachment("document", "id", size=DOWNLOAD_LIMIT + 1), tmp_path / "x")
    bot.get_file.assert_not_called()

    async def oversized(_, output):
        output.write(b"x" * (DOWNLOAD_LIMIT + 1))

    bot.download_file.side_effect = oversized
    with pytest.raises(MediaTooLarge):
        await download(bot, Attachment("document", "id"), tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_receive_media_and_fallback():
    relay = from_telegram(
        message(voice={"file_id": "a", "file_unique_id": "b", "duration": 2, "file_size": 123})
    )
    assert relay.attachments[0] == Attachment("voice", "a", size=123, duration=2000)
    relay = from_telegram(message(contact={"phone_number": "+1000", "first_name": "Name"}))
    assert "Контакт:" in relay.text
    relay = from_telegram(message(location={"latitude": 1, "longitude": 2}))
    assert "https://maps.google.com/?q=1.0,2.0" in relay.text


async def test_partial_send_retry_keeps_successful_parts():
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=[
                SimpleNamespace(message_id=1),
                ValueError("retry"),
                SimpleNamespace(message_id=2),
            ]
        )
    )
    progress = []
    relay = RelayMessage("a" * 5000)
    with pytest.raises(ValueError):
        await send(bot, 10, 2, relay, progress=progress)
    result = await send(bot, 10, 2, relay, progress=progress)
    assert [m.message_id for m in result] == [1, 2]
    assert bot.send_message.await_count == 3
    assert len(bot.send_message.call_args_list[-1].kwargs["text"]) == 904


async def test_edit_growing_caption_keeps_entire_text(storage):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageText

    _, sessions, _ = storage
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(
            side_effect=TelegramBadRequest(
                method=EditMessageText(chat_id=10, message_id=1, text="x"),
                message="Bad Request: there is no text in the message to edit",
            )
        ),
        edit_message_caption=AsyncMock(),
        send_message=AsyncMock(
            side_effect=[SimpleNamespace(message_id=2), SimpleNamespace(message_id=3)]
        ),
    )
    adapter = TgBot("", SimpleNamespace(inbox_chat_id=10, owner_tg_user_id=1), sessions, bot=bot)
    ids = await adapter.edit_parts([1], RelayMessage("a" * 5000), topic_id=2)
    assert ids == [1, 2, 3]
    assert bot.edit_message_caption.call_args.kwargs["caption"] == ""
    assert "".join(call.kwargs["text"] for call in bot.send_message.call_args_list) == "a" * 5000


async def test_start_cannot_rebind_existing_inbox(storage):
    from maxgate.db.models import Account
    from maxgate.relay.storage import RelayStorage

    _, sessions, _ = storage
    async with sessions.begin() as session:
        account = await session.get(Account, 1)
        account.inbox_mode = "supergroup"
        account.inbox_chat_id = 10
    store = RelayStorage(1, sessions)
    link = await store.ensure_chat(50, "CHAT", "Title", 0)
    await store.change_chat(link.id, topic_id=2)
    await store.link_messages(link.id, 70, [1], "max_to_tg")
    fake = SimpleNamespace(send_message=AsyncMock(), get_me=AsyncMock())
    adapter = TgBot("", account, sessions, bot=fake)
    await adapter._start(
        message(text="/start", chat={"id": 999, "type": "supergroup", "is_forum": True})
    )
    fake.get_me.assert_not_called()
    assert "Operator" in fake.send_message.call_args.kwargs["text"]
    assert account.inbox_chat_id == 10
    assert (await store.chat(link_id=link.id)).topic_id == 2
    assert len(await store.messages(link.id)) == 1
