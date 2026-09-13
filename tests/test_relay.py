import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pymax import Message
from sqlalchemy import select

from maxgate.db.models import Account, MessageLink
from maxgate.domain import Attachment, RelayMessage
from maxgate.relay.engine import RelayEngine
from maxgate.relay.queue import ChatQueues, Job


async def no_sleep(_):
    await asyncio.sleep(0)


def max_message(message_id=1, chat_id=10, sender=2, **kwargs):
    return Message(
        id=message_id,
        chat_id=chat_id,
        sender=sender,
        time=1000 + message_id,
        type="USER",
        text="hello",
        **kwargs,
    )


def tg_message(message_id=100, topic_id=200, album=None):
    return NS(message_id=message_id, message_thread_id=topic_id, media_group_id=album)


class FakeMax:
    me_id = 1

    def __init__(self):
        self.chat = NS(
            id=10,
            title="Group",
            type="CHAT",
            participants={},
            participants_count=3,
            last_event_time=0,
        )
        self.get_chat = AsyncMock(return_value=self.chat)
        self.fetch_chats = AsyncMock(return_value=[self.chat])
        self.fetch_history = AsyncMock(return_value=[])
        self.user_name = AsyncMock(return_value="Sender")
        self.send = AsyncMock(return_value=NS(id=999))
        self.edit = AsyncMock()
        self.download_attachment = AsyncMock(side_effect=self.download)

    async def download(self, chat_id, message_id, source, dest):
        dest.write_bytes(b"max media")
        return dest


class FakeTg:
    def __init__(self):
        self.sent = []
        self.failures = []
        self.create_topic = AsyncMock(return_value=200)
        self.edit_topic = AsyncMock()
        self.edit_parts = AsyncMock()
        self.deletion_note = AsyncMock()
        self.note = AsyncMock()
        self.download = AsyncMock(side_effect=self._download)

    async def send(self, topic_id, message, *, progress=None):
        if progress:
            return progress[0]
        if self.failures:
            raise self.failures.pop(0)
        result = [NS(message_id=100 + len(self.sent))]
        self.sent.append((topic_id, message))
        if progress is not None:
            progress.append(result)
        return result

    async def _download(self, attachment, dest):
        dest.write_bytes(b"tg media")
        return dest


@pytest_asyncio.fixture
async def relay(storage, tmp_path):
    _, sessions, _ = storage
    async with sessions.begin() as session:
        account = await session.get(Account, 1)
        account.inbox_chat_id = 1
        account.state = "active"
    engine = RelayEngine(
        account,
        sessions,
        FakeMax(),
        FakeTg(),
        tmp_path,
        AsyncMock(),
        queues=ChatQueues(sleep=no_sleep),
        album_delay=0.01,
    )
    yield engine
    await engine.close(timeout=0.1)


async def test_fifo_and_independent_workers():
    queues = ChatQueues(sleep=no_sleep)
    gate = asyncio.Event()
    calls = []

    async def blocked():
        await gate.wait()
        calls.append("a")

    async def record(value):
        calls.append(value)

    queues.submit(1, Job(blocked, AsyncMock()))
    queues.submit(1, Job(lambda: record("b"), AsyncMock()))
    queues.submit(2, Job(lambda: record("c"), AsyncMock()))
    for _ in range(5):
        await asyncio.sleep(0)
    assert calls == ["c"]
    gate.set()
    await queues.drain()
    assert calls == ["c", "a", "b"]
    await queues.close()


async def test_retry_schedule_429_outside_attempt_count():
    class RetryAfter(Exception):
        retry_after = 7

    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    queues = ChatQueues(sleep=sleep)
    run = AsyncMock(side_effect=[RetryAfter(), ValueError(), ValueError(), ValueError()])
    failed = AsyncMock()
    await queues.execute(Job(run, failed))
    assert run.await_count == 4
    assert sleeps == [1, 7, 5, 30]
    failed.assert_awaited_once()


async def test_max_relay_echo_reply_and_timestamp(relay):
    await relay.accept_max(max_message())
    await relay.queues.drain()
    link = await relay.store.chat(max_chat_id=10)
    assert link.topic_id == 200
    assert link.last_relayed_time == 1001
    assert relay.tg.sent[0][1].text == "Sender\nhello"
    await relay.accept_max(max_message())
    await relay.queues.drain()
    assert len(relay.tg.sent) == 1
    await relay.store.link_messages(link.id, 3, [102], "tg_to_max")
    await relay.accept_max(max_message(3, sender=1))
    await relay.queues.drain()
    assert len(relay.tg.sent) == 1
    reply = max_message(
        4, link={"type": "REPLY", "chatId": 10, "message": {"id": 1, "time": 1001, "type": "USER"}}
    )
    await relay.accept_max(reply)
    await relay.queues.drain()
    assert relay.tg.sent[-1][1].reply_to == 100
    await relay.accept_max(max_message(5, sender=1))
    await relay.queues.drain()
    assert relay.tg.sent[-1][1].text.startswith("Вы\n")


async def test_muted_is_one_way_and_channel_filter(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, muted=True, topic_id=200)
    await relay.accept_max(max_message())
    await relay.accept_tg(tg_message(), RelayMessage("outgoing"))
    await relay.queues.drain()
    assert relay.tg.sent == []
    relay.max.send.assert_awaited_once()
    await relay.store.change_chat(link.id, muted=False, max_chat_type="CHANNEL")
    await relay.accept_max(max_message(2))
    await relay.queues.drain()
    assert relay.tg.sent == []
    relay.account.relay_channels = True
    await relay.accept_max(max_message(3))
    await relay.queues.drain()
    assert len(relay.tg.sent) == 1


async def test_tg_album_reserves_fifo_and_replies(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 5, [50], "max_to_tg")
    attachment = Attachment("photo", "file-id")
    await relay.accept_tg(
        tg_message(100, album="album"),
        RelayMessage("caption", attachments=[attachment], reply_to=50),
    )
    await relay.accept_tg(tg_message(101, album="album"), RelayMessage(attachments=[attachment]))
    await relay.accept_tg(tg_message(102), RelayMessage("next"))
    await relay.queues.drain()
    calls = relay.max.send.call_args_list
    assert len(calls) == 2
    assert len(calls[0].args[1].attachments) == 2
    assert calls[0].args[1].reply_to == 5
    assert calls[1].args[1].text == "next"
    assert len(await relay.store.messages(link.id, max_id=999)) == 3
    assert list(relay.tmp.iterdir()) == []


async def test_missing_topic_recreated_once_and_reply_cleared(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=10)
    await relay.store.link_messages(link.id, 3, [12], "max_to_tg")
    relay.tg.failures = [ValueError("Bad Request: message thread not found")]
    await relay.accept_max(
        max_message(
            4, link={"type": "REPLY", "chatId": 10, "message": {"id": 3, "time": 1, "type": "USER"}}
        )
    )
    await relay.queues.drain()
    relay.tg.create_topic.assert_awaited_once()
    assert relay.tg.sent[0][0] == 200
    assert relay.tg.sent[0][1].reply_to is None
    assert await relay.store.messages(link.id, max_id=3) == []


async def test_inbox_expiry_and_release(relay):
    relay.account.inbox_chat_id = None
    relay.inbox_ready.clear()
    relay.inbox_ttl = 0.01
    await relay.accept_max(max_message())
    await relay.queues.drain()
    assert relay.tg.sent == []
    assert "discarded" in relay.event.call_args.args[0]
    relay.inbox_ttl = 3600
    await relay.accept_max(max_message(2))
    relay.account.inbox_chat_id = 1
    await relay.inbox_bound()
    await relay.queues.drain()
    assert len(relay.tg.sent) == 1


async def test_catchup_initial_skip_then_limit_and_history_links(relay):
    await relay.catch_up()
    await relay.queues.drain()
    relay.max.fetch_history.assert_not_called()
    link = await relay.store.chat(max_chat_id=10)
    relay.max.chat.last_event_time = link.last_relayed_time + 200
    history = [max_message(n) for n in range(1, 102)]
    relay.max.fetch_history.return_value = history
    await relay.catch_up()
    await relay.queues.drain()
    assert len(relay.tg.sent) == 101  # 100 сообщений + Note
    assert "пропущено ещё 1" in relay.tg.sent[-1][1].text
    assert relay.tg.sent[0][1].text.startswith("[01.01")
    assert len(await relay.store.messages(link.id)) == 100
    relay.max.fetch_history.return_value = [max_message(1000)]
    await relay.history(link, 200)
    await relay.queues.drain()
    assert relay.max.fetch_history.call_args.kwargs == {"backward": 100}
    assert len(await relay.store.messages(link.id, max_id=1000)) == 1


async def test_edits_deletes_and_owner_rename(relay):
    await relay.accept_max(max_message())
    await relay.queues.drain()
    link = await relay.store.chat(max_chat_id=10)
    await relay.max_edit(max_message().model_copy(update={"text": "edited"}))
    await relay.max_delete(NS(chat_id=10, message_ids=[1]))
    await relay.queues.drain()
    assert relay.tg.edit_parts.await_count == 1
    relay.tg.deletion_note.assert_awaited_once()
    relay.tg.deletion_note.assert_awaited_once_with(200, 100)
    await relay.chat_update(NS(id=10, title="New"))
    await relay.queues.drain()
    relay.tg.edit_topic.assert_awaited_once_with(200, "New")
    await relay.topic_edited(200, "Owner's title")
    await relay.chat_update(NS(id=10, title="Another"))
    await relay.queues.drain()
    assert relay.tg.edit_topic.await_count == 1
    assert (await relay.store.chat(link_id=link.id)).max_title == "Another"


async def test_failed_relay_note_and_temp_cleanup(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    relay.max.send.side_effect = ValueError(
        "upload rejected; token=do-not-print; payload={sensitive: data}"
    )
    await relay.accept_tg(tg_message(), RelayMessage("x", attachments=[Attachment("photo", "id")]))
    await relay.queues.drain()
    assert relay.max.send.await_count == 3
    assert "upload rejected" in relay.tg.sent[-1][1].text
    assert "do-not-print" not in relay.tg.sent[-1][1].text
    assert "sensitive" not in relay.tg.sent[-1][1].text
    assert relay.tg.sent[-1][1].reply_to == 100
    assert list(relay.tmp.iterdir()) == []


async def test_expired_links_do_not_reply_and_cleanup(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    async with relay.store.sessions.begin() as session:
        session.add(
            MessageLink(
                account_id=1,
                chat_link_id=link.id,
                max_message_id=10,
                tg_message_id=50,
                part=0,
                direction="max_to_tg",
                created_at=datetime.now(UTC) - timedelta(days=91),
            )
        )
    await relay.accept_tg(tg_message(), RelayMessage("reply", reply_to=50))
    await relay.queues.drain()
    assert relay.max.send.call_args.args[1].reply_to is None
    await relay.store.cleanup()
    async with relay.store.sessions() as session:
        assert (
            await session.scalar(select(MessageLink).where(MessageLink.tg_message_id == 50)) is None
        )


async def test_commands_and_tg_edit(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 5, [100], "tg_to_max")
    for command in ("/status", "/mute", "/unmute", "/history nope", "/info"):
        source = tg_message()
        source.text = command
        assert await relay.command(source)
    assert not (await relay.store.chat(link_id=link.id)).muted
    await relay.tg_edit(tg_message(), RelayMessage("edited"))
    await relay.queues.drain()
    relay.max.edit.assert_awaited_once_with(10, 5, "edited", entities=[])


async def test_late_inbox_does_not_release_expired_message(relay):
    now = [0]
    relay.clock = lambda: now[0]
    relay.account.inbox_chat_id = None
    relay.bound_at = None
    relay.inbox_ready.clear()
    relay.inbox_ttl = 10
    gate = asyncio.Event()

    async def delayed(_):
        await gate.wait()

    relay.queues.sleep = delayed
    await relay.accept_max(max_message())
    now[0] = 11
    relay.account.inbox_chat_id = 1
    await relay.inbox_bound()
    gate.set()
    await relay.queues.drain()
    assert relay.tg.sent == []


async def test_shutdown_cancels_blocked_worker():
    queues = ChatQueues(sleep=no_sleep)
    canceled = asyncio.Event()

    async def blocked():
        try:
            await asyncio.Future()
        finally:
            canceled.set()

    queues.submit(1, Job(blocked, AsyncMock()))
    for _ in range(3):
        await asyncio.sleep(0)
    await queues.close(timeout=0.001)
    assert canceled.is_set()
    assert all(task.done() for task in queues.workers.values())


async def test_catchup_continues_after_poison_message(relay):
    link = await relay._link(10)
    relay.max.chat.last_event_time = 2000
    relay.max.fetch_history.return_value = [max_message(n) for n in range(1, 4)]
    send = relay.tg.send
    attempts = []

    async def reject_one(topic, message, **kwargs):
        if message.text.endswith("bad"):
            attempts.append(1)
            raise ValueError("rejected")
        return await send(topic, message, **kwargs)

    relay.max.fetch_history.return_value[0].text = "bad"
    relay.tg.send = reject_one
    await relay.catch_up()
    await relay.accept_max(max_message(4))
    await relay.queues.drain()
    assert len(attempts) == 3
    assert [r.max_message_id for r in await relay.store.messages(link.id)] == [2, 3, 4]
    await relay.catch_up()
    await relay.queues.drain()
    assert len([m for _, m in relay.tg.sent if m.text.endswith("hello")]) == 3


async def test_concurrent_max_events_keep_ingress_order(relay):
    entered, release = asyncio.Event(), asyncio.Event()

    async def get_chat(_):
        entered.set()
        await release.wait()
        return relay.max.chat

    relay.max.get_chat.side_effect = get_chat
    first = asyncio.create_task(relay.accept_max(max_message()))
    await entered.wait()
    edit = asyncio.create_task(relay.max_edit(max_message().model_copy(update={"text": "edited"})))
    delete = asyncio.create_task(relay.max_delete(NS(chat_id=10, message_ids=[1])))
    update = asyncio.create_task(relay.chat_update(NS(id=10, title="Renamed")))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, edit, delete, update)
    await relay.queues.drain()
    assert relay.tg.edit_parts.await_count == 1
    relay.tg.deletion_note.assert_awaited_once()
    assert relay.tg.edit_parts.call_args_list[0].args[1].text == "Sender\nedited"
    relay.tg.edit_topic.assert_awaited_once_with(200, "Renamed")


async def test_album_caption_edit_preserves_other_parts(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.accept_tg(tg_message(100, album="a"), RelayMessage("A"))
    await relay.accept_tg(tg_message(101, album="a"), RelayMessage("B"))
    await relay.queues.drain()
    await relay.tg_edit(tg_message(101), RelayMessage("edited B"))
    await relay.queues.drain()
    relay.max.edit.assert_awaited_with(10, 999, "A\nedited B", entities=[])
    await relay.tg_edit(tg_message(101), RelayMessage(""))
    await relay.queues.drain()
    relay.max.edit.assert_awaited_with(10, 999, "A", entities=[])


async def test_download_limit_preserves_text_and_other_media(relay):
    from maxgate.max.media import MediaTooLarge

    async def download(chat, msg, source, dest):
        if source.name == "large.bin":
            raise MediaTooLarge(60 * 1024 * 1024)
        dest.write_bytes(b"ok")

    relay.max.download_attachment.side_effect = download
    await relay.accept_max(
        max_message(
            attaches=[
                {"_type": "FILE", "fileId": 1, "name": "large.bin", "size": 1, "token": "fake"},
                {"_type": "FILE", "fileId": 2, "name": "ok.bin", "size": 2, "token": "fake"},
            ]
        )
    )
    await relay.queues.drain()
    assert relay.max.download_attachment.await_count == 2
    message = relay.tg.sent[0][1]
    assert "hello" in message.text and "large.bin" in message.text
    assert "62914560" in message.text
    assert len(message.attachments) == 1
    assert list(relay.tmp.iterdir()) == []


async def test_shutdown_reports_pending_and_running_jobs(caplog):
    queues = ChatQueues(sleep=no_sleep)
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Future()

    failed = AsyncMock()
    queues.submit(1, Job(blocked, failed, direction="tg_to_max"))
    queues.submit(1, Job(AsyncMock(), failed, direction="tg_to_max"))
    await entered.wait()
    assert await queues.close(timeout=0.001) == 2
    assert failed.await_count == 2
    assert "tg_to_max" in caplog.text and "2" in caplog.text
    assert queues.size == 0


async def test_forward_content_media_source_and_outer_mapping(relay):
    original = max_message(
        40,
        chat_id=60,
        attaches=[
            {"_type": "FILE", "fileId": 1, "name": "file.bin", "size": 2, "token": "fake"},
        ],
    ).model_copy(update={"text": "inside", "elements": []})
    outer = max_message(
        50, link={"type": "FORWARD", "chatId": 60, "chatName": "source", "message": original}
    )
    outer.text = ""
    await relay.accept_max(outer)
    await relay.queues.drain()
    assert "Переслано от source\ninside" in relay.tg.sent[0][1].text
    assert relay.max.download_attachment.call_args.args[:2] == (60, 40)
    link = await relay.store.chat(max_chat_id=10)
    assert len(await relay.store.messages(link.id, max_id=50)) == 1
    assert await relay.store.messages(link.id, max_id=40) == []


async def test_shutdown_tg_jobs_notify_owner(relay, caplog):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    entered = asyncio.Event()

    async def blocked(*_):
        entered.set()
        await asyncio.Future()

    relay.max.send.side_effect = blocked
    await relay.accept_tg(tg_message(100), RelayMessage("first"))
    await relay.accept_tg(tg_message(101), RelayMessage("second"))
    await entered.wait()
    assert await relay.close(0.001) == 2
    assert sorted(m.reply_to for _, m in relay.tg.sent) == [100, 101]
    assert all(m.text.startswith("❌") for _, m in relay.tg.sent)
    assert "lost 2 jobs direction=tg_to_max" in caplog.text


async def test_history_requires_count_links_dedup_and_reply_edit_delete(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200, muted=True)
    for text in ["/history", "/history nope", "/history 0", "/history -1"]:
        command = tg_message()
        command.text = text
        await relay.command(command)
        assert relay.tg.sent[-1][1].text == "укажите число сообщений"
    relay.max.fetch_history.assert_not_called()
    relay.max.fetch_history.return_value = [max_message(123)]
    command.text = "/history 200"
    await relay.command(command)
    await relay.queues.drain()
    assert relay.max.fetch_history.call_args.kwargs == {"backward": 100}
    rows = await relay.store.messages(link.id, max_id=123)
    assert len(rows) == 1 and rows[0].direction == "max_to_tg"
    assert relay.tg.sent[-1][1].text.startswith("[01.01")
    assert (await relay.store.chat(link_id=link.id)).last_relayed_time == 0
    sent = len(relay.tg.sent)
    await relay.command(command)
    await relay.queues.drain()
    assert len(relay.tg.sent) == sent
    await relay.accept_tg(tg_message(999), RelayMessage("reply", reply_to=rows[0].tg_message_id))
    await relay.max_edit(max_message(123).model_copy(update={"text": "changed"}))
    await relay.max_delete(NS(chat_id=10, message_ids=[123]))
    await relay.queues.drain()
    assert relay.max.send.call_args.args[1].reply_to == 123
    assert relay.tg.edit_parts.await_count == 1
    relay.tg.deletion_note.assert_awaited_once()


async def test_reaction_add_replace_remove_and_unknown_link(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 123, [100], "max_to_tg")
    relay.max.add_reaction = AsyncMock()
    relay.max.remove_reaction = AsyncMock()
    for emoji in ["👍", "❤", None]:
        await relay.tg_reaction(
            NS(message_id=100, new_reaction=[NS(type="emoji", emoji=emoji)] if emoji else [])
        )
    await relay.queues.drain()
    assert [c.args for c in relay.max.add_reaction.call_args_list] == [
        (10, 123, "👍"),
        (10, 123, "❤"),
    ]
    relay.max.remove_reaction.assert_awaited_once_with(10, 123)
    await relay.tg_reaction(NS(message_id=404, new_reaction=[]))
    relay.tg.note.assert_awaited_once_with("⛔ сообщение не связано с MAX", reply_to=404)


async def test_reaction_error_sends_one_note(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 123, [100], "tg_to_max")
    relay.max.add_reaction = AsyncMock(side_effect=ValueError("unsupported"))
    await relay.tg_reaction(NS(message_id=100, new_reaction=[NS(type="emoji", emoji="🧪")]))
    await relay.queues.drain()
    assert relay.max.add_reaction.await_count == 1
    relay.tg.note.assert_awaited_once()
    assert relay.tg.note.call_args.args[0].startswith("⛔")
    assert relay.tg.note.call_args.kwargs == dict(topic_id=200, reply_to=100)


async def test_reaction_custom_emoji_rejected_without_max(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 123, [100], "max_to_tg")
    relay.max.add_reaction = AsyncMock()
    await relay.tg_reaction(NS(message_id=100, new_reaction=[NS(type="custom_emoji")]))
    await relay.queues.drain()
    relay.max.add_reaction.assert_not_called()
    relay.tg.note.assert_awaited_once()


async def test_formatted_album_send_and_edit(relay):
    from maxgate.domain import Entity

    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.accept_tg(tg_message(100, album="a"), RelayMessage("😀A", [Entity("bold", 2, 1)]))
    await relay.accept_tg(tg_message(101, album="a"), RelayMessage("B", [Entity("italic", 0, 1)]))
    await relay.queues.drain()
    sent = relay.max.send.call_args.args[1]
    assert sent.text == "😀A\nB"
    assert sent.entities == [Entity("bold", 2, 1), Entity("italic", 4, 1)]
    await relay.tg_edit(tg_message(101), RelayMessage("BC", [Entity("code", 0, 2)]))
    await relay.queues.drain()
    relay.max.edit.assert_awaited_with(
        10, 999, "😀A\nBC", entities=[Entity("bold", 2, 1), Entity("code", 4, 2)]
    )


async def test_delete_multipart_notes_only_first_and_retry_progress(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 5, [100, 101], "max_to_tg")
    await relay.store.link_messages(link.id, 6, [102], "max_to_tg")
    relay.tg.deletion_note.side_effect = [None, ValueError("transient"), None]
    await relay.max_delete(NS(chat_id=10, message_ids=[5, 6]))
    await relay.queues.drain()
    assert [c.args for c in relay.tg.deletion_note.call_args_list] == [
        (200, 100),
        (200, 102),
        (200, 102),
    ]
    relay.tg.edit_parts.assert_not_called()


async def test_permanent_max_api_error_has_no_retry():
    from pymax.exceptions import ApiError

    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    queues = ChatQueues(sleep=sleep)
    for code in [
        "error.message.like.unknown.like",
        "error.message.invalid",
        "other.server.rejection",
    ]:
        run = AsyncMock(side_effect=ApiError(opcode=178, error=code))
        failed = AsyncMock()
        await queues.execute(Job(run, failed))
        assert run.await_count == 1
        failed.assert_awaited_once()
    assert sleeps == [1, 1, 1]


async def test_blocked_reaction_does_not_delay_message(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 123, [100], "max_to_tg")
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args):
        entered.set()
        await release.wait()

    relay.max.add_reaction = AsyncMock(side_effect=blocked)
    await relay.tg_reaction(NS(message_id=100, new_reaction=[NS(type="emoji", emoji="👍")]))
    await asyncio.wait_for(entered.wait(), 1)
    try:
        await relay.accept_max(max_message(999))
        for _ in range(100):
            if relay.tg.sent:
                break
            await asyncio.sleep(0.005)
        assert relay.tg.sent, "Reaction blocked message lane"
    finally:
        release.set()
    await relay.queues.drain()


async def test_unsupported_reaction_removes_previous_and_notifies_once(relay):
    from pymax.exceptions import ApiError

    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 123, [100], "max_to_tg")
    relay.max.add_reaction = AsyncMock(
        side_effect=ApiError(opcode=178, error="error.message.like.unknown.like")
    )
    relay.max.remove_reaction = AsyncMock()
    await relay.tg_reaction(NS(message_id=100, new_reaction=[NS(type="emoji", emoji="🧪")]))
    await relay.queues.drain()
    assert relay.max.add_reaction.await_count == 2
    assert [c.args[2] for c in relay.max.add_reaction.call_args_list] == ["🧪", "🧪\ufe0f"]
    relay.max.remove_reaction.assert_awaited_once_with(10, 123)
    relay.tg.note.assert_awaited_once_with(
        "⛔ MAX не поддерживает реакцию 🧪, реакция в MAX снята", topic_id=200, reply_to=100
    )


async def test_transient_transport_retries_and_wrapped_api_refusal():
    from pymax.exceptions import ApiError, UploadError

    queues = ChatQueues(sleep=no_sleep)
    for error in [
        TimeoutError(),
        ConnectionError(),
        ApiError(opcode=64, error="error.request.timeout"),
    ]:
        run = AsyncMock(side_effect=[error, None])
        failed = AsyncMock()
        await queues.execute(Job(run, failed))
        assert run.await_count == 2
        failed.assert_not_called()
    wrapped = UploadError("upload failed")
    wrapped.__cause__ = ApiError(opcode=64, error="error.message.invalid")
    run = AsyncMock(side_effect=wrapped)
    failed = AsyncMock()
    await queues.execute(Job(run, failed))
    run.assert_awaited_once()
    failed.assert_awaited_once()


async def test_send_and_edit_api_refusals_are_final(relay):
    from pymax.exceptions import ApiError

    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    relay.max.send.side_effect = ApiError(opcode=64, error="error.message.invalid")
    await relay.accept_tg(tg_message(100), RelayMessage("send"))
    await relay.queues.drain()
    relay.max.send.assert_awaited_once()
    await relay.store.link_messages(link.id, 123, [101], "tg_to_max")
    relay.max.edit.side_effect = ApiError(opcode=67, error="error.message.invalid")
    await relay.tg_edit(tg_message(101), RelayMessage("edit"))
    await relay.queues.drain()
    relay.max.edit.assert_awaited_once()
    assert len(relay.tg.sent) == 2


async def test_reaction_removal_failure_is_reported_once(relay):
    from pymax.exceptions import ApiError

    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 123, [100], "max_to_tg")
    relay.max.add_reaction = AsyncMock(
        side_effect=ApiError(opcode=178, error="error.message.invalid")
    )
    relay.max.remove_reaction = AsyncMock(side_effect=TimeoutError())
    await relay.tg_reaction(NS(message_id=100, new_reaction=[NS(type="emoji", emoji="🧪")]))
    await relay.queues.drain()
    assert relay.max.add_reaction.await_count == 2
    assert [c.args[2] for c in relay.max.add_reaction.call_args_list] == ["🧪", "🧪\ufe0f"]
    relay.max.remove_reaction.assert_awaited_once()
    relay.tg.note.assert_awaited_once()
    assert "снять реакцию в MAX не удалось" in relay.tg.note.call_args.args[0]


async def test_reaction_lane_keeps_order_with_single_transport_attempt(relay):
    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 123, [100], "max_to_tg")
    calls = []

    async def add(chat, message, emoji):
        calls.append(emoji)
        if emoji == "👍":
            raise TimeoutError()

    relay.max.add_reaction = AsyncMock(side_effect=add)
    for emoji in ["👍", "❤"]:
        await relay.tg_reaction(NS(message_id=100, new_reaction=[NS(type="emoji", emoji=emoji)]))
    await relay.queues.drain()
    assert calls == ["👍", "❤"]
    relay.tg.note.assert_awaited_once()


async def test_reaction_selector_alternate_succeeds_without_removal(relay):
    from pymax.exceptions import ApiError

    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 123, [100], "max_to_tg")
    relay.max.remove_reaction = AsyncMock()
    for original, alternate in [("❤", "❤️"), ("❤️", "❤")]:
        relay.max.add_reaction = AsyncMock(
            side_effect=[ApiError(opcode=178, error="error.message.like.unknown.like"), None]
        )
        await relay.tg_reaction(NS(message_id=100, new_reaction=[NS(type="emoji", emoji=original)]))
        await relay.queues.drain()
        assert [c.args[2] for c in relay.max.add_reaction.call_args_list] == [original, alternate]
    relay.max.remove_reaction.assert_not_called()
    relay.tg.note.assert_not_called()


async def test_reaction_selector_transport_error_does_not_claim_unsupported(relay):
    from pymax.exceptions import ApiError

    link = await relay._link(10)
    await relay.store.change_chat(link.id, topic_id=200)
    await relay.store.link_messages(link.id, 123, [100], "max_to_tg")
    relay.max.add_reaction = AsyncMock(
        side_effect=[ApiError(opcode=178, error="error.message.like.unknown.like"), TimeoutError()]
    )
    relay.max.remove_reaction = AsyncMock()
    await relay.tg_reaction(NS(message_id=100, new_reaction=[NS(type="emoji", emoji="❤")]))
    await relay.queues.drain()
    assert relay.max.add_reaction.await_count == 2
    relay.max.remove_reaction.assert_not_called()
    relay.tg.note.assert_awaited_once()
    assert relay.tg.note.call_args.args[0] == "⛔ не удалось изменить реакцию в MAX"


@pytest.mark.parametrize("permanent", [True, False])
async def test_history_continues_after_failure_before_live_events(relay, permanent):
    from pymax.exceptions import ApiError

    link = await relay._link(10)
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(*args, **kwargs):
        entered.set()
        await release.wait()
        return [max_message(n).model_copy(update={"text": str(n)}) for n in (3, 2, 1)]

    relay.max.fetch_history.side_effect = fetch
    send = relay.tg.send
    attempts = {"2": 0, "3": 0}

    async def reject_one(topic, message, **kwargs):
        number = message.text[-1:]
        if number in attempts:
            attempts[number] += 1
            if number == "2":
                if permanent:
                    raise ApiError(opcode=64, error="error.message.invalid")
                raise TimeoutError()
            if attempts[number] < 3:
                raise TimeoutError()
        return await send(topic, message, **kwargs)

    relay.tg.send = reject_one
    await relay.history(link, 50)
    await asyncio.wait_for(entered.wait(), 1)
    try:
        await relay.accept_max(max_message(4).model_copy(update={"text": "4"}))
    finally:
        release.set()
    await relay.queues.drain()
    assert [r.max_message_id for r in await relay.store.messages(link.id)] == [1, 3, 4]
    assert attempts == {"2": 1 if permanent else 3, "3": 3}
    delivered = [message for _, message in relay.tg.sent]
    assert len(delivered) == 4
    assert delivered[0].text.endswith("1")
    assert delivered[1].text.startswith("ℹ️ ")
    assert delivered[2].text.endswith("3")
    assert delivered[3].text.endswith("4")
    relay.max.fetch_history.assert_awaited_once_with(10, backward=50)


@pytest.mark.parametrize("unnamed_only", [False, True])
@pytest.mark.parametrize("mode", ["live", "catch_up", "history"])
@pytest.mark.parametrize("failure", ["permanent", "wrapped", "no_url"])
async def test_inaccessible_attachment_preserves_message_and_other_media(
    relay, mode, failure, unnamed_only
):
    from pymax.exceptions import ApiError

    from maxgate.max.client import MaxClient

    link = await relay._link(10)
    relay.max.chat.last_event_time = 2000
    message = max_message(
        attaches=[
            {"_type": "FILE", "fileId": 1, "name": "denied.bin", "size": 1, "token": "fake"},
            {"_type": "FILE", "fileId": 2, "name": "ok.bin", "size": 1, "token": "fake"},
        ]
    )

    if unnamed_only:
        message.attaches = message.attaches[:1]
        message.attaches[0].name = None

    async def download(chat, msg, source, dest):
        if source.file_id == 1:
            if failure == "no_url":
                adapter = NS(client=NS(get_file_by_id=AsyncMock(return_value=None)))
                return await MaxClient.download_attachment(adapter, chat, msg, source, dest)
            error = ApiError(opcode=88, error="error.user.file.access")
            if failure == "wrapped":
                raise RuntimeError("wrapped") from error
            raise error
        dest.write_bytes(b"ok")

    relay.max.download_attachment.side_effect = download
    relay.max.fetch_history.return_value = [message]
    if mode == "live":
        await relay.accept_max(message)
    elif mode == "catch_up":
        await relay.catch_up()
    else:
        await relay.history(link, 50)
    await relay.queues.drain()
    assert relay.max.download_attachment.await_count == (1 if unnamed_only else 2)
    assert len(relay.tg.sent) == 1
    delivered = relay.tg.sent[0][1]
    assert "hello" in delivered.text
    detail = "нет URL" if failure == "no_url" else "error.user.file.access"
    name = "без имени" if unnamed_only else "denied.bin"
    assert f"ℹ️ Файл {name}: нет доступа ({detail})" in delivered.text
    assert [a.name for a in delivered.attachments] == ([] if unnamed_only else ["ok.bin"])
    assert [r.max_message_id for r in await relay.store.messages(link.id)] == [1]
    assert list(relay.tmp.iterdir()) == []


@pytest.mark.parametrize("failure", ["timeout", "transport", "api_timeout", "other_value_error"])
async def test_attachment_transient_failure_still_retries(relay, failure):
    from aiohttp import ClientConnectionError
    from pymax.exceptions import ApiError

    errors = {
        "timeout": TimeoutError(),
        "transport": ClientConnectionError(),
        "api_timeout": ApiError(opcode=88, error="error.request.timeout"),
        "other_value_error": ValueError("unrelated download error"),
    }
    attempts = 0

    async def download(chat, msg, source, dest):
        nonlocal attempts
        attempts += 1
        dest.write_bytes(b"partial" if attempts < 3 else b"ok")
        if attempts < 3:
            raise errors[failure]

    relay.max.download_attachment.side_effect = download
    await relay.accept_max(
        max_message(
            attaches=[
                {"_type": "FILE", "fileId": 1, "name": "retry.bin", "size": 1, "token": "fake"},
            ]
        )
    )
    await relay.queues.drain()
    assert attempts == 3
    assert len(relay.tg.sent) == 1
    message = relay.tg.sent[0][1]
    assert message.text == "Sender\nhello"
    assert [a.name for a in message.attachments] == ["retry.bin"]
    assert list(relay.tmp.iterdir()) == []
