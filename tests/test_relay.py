import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

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


async def test_catchup_initial_skip_then_limit_and_history_no_links(relay):
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
    assert await relay.store.messages(link.id, max_id=1000) == []


async def test_edits_deletes_and_owner_rename(relay):
    await relay.accept_max(max_message())
    await relay.queues.drain()
    link = await relay.store.chat(max_chat_id=10)
    await relay.max_edit(max_message().model_copy(update={"text": "edited"}))
    await relay.max_delete(NS(chat_id=10, message_ids=[1]))
    await relay.queues.drain()
    assert relay.tg.edit_parts.await_count == 2
    assert relay.tg.edit_parts.call_args.args[1].text == "🗑 удалено"
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
    relay.max.edit.assert_awaited_once_with(10, 5, "edited")


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
