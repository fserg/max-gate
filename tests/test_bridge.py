import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from sqlalchemy import select

from maxgate.bridge.api import InternalApi
from maxgate.bridge.runner import AccountRunner
from maxgate.bridge.storage import AccountStorage
from maxgate.bridge.supervisor import Supervisor
from maxgate.config import Settings
from maxgate.db.models import AccountEvent, MaxSession
from maxgate.max.client import GateClient, SessionLost
from maxgate.max.providers import PasswordProvider, SmsCodeProvider
from maxgate.max.store import SessionStore
from maxgate.relay.storage import RelayStorage


async def test_event_retention(storage):
    _, sessions, _ = storage
    async with sessions.begin() as session:
        session.add_all(
            AccountEvent(account_id=1, level="INFO", message="old") for _ in range(1005)
        )
    await AccountStorage(sessions).event(1, "latest")
    async with sessions() as session:
        events = list(
            await session.scalars(select(AccountEvent).where(AccountEvent.account_id == 1))
        )
    assert len(events) == 1000
    assert events[-1].message == "latest"


async def test_supervisor_five_failures_then_error(storage, tmp_path):
    _, sessions, crypto = storage
    delays = []
    runs = []

    class FailingRunner:
        started_at = None

        def __init__(self, *args, login):
            runs.append(login)

        async def run(self):
            raise ValueError("simulated transport failure")

    async def sleep(delay):
        delays.append(delay)

    supervisor = Supervisor(
        sessions, crypto, NS(data_dir=tmp_path), runner_factory=FailingRunner, sleep=sleep
    )
    await supervisor.launch(1, login=True)
    await supervisor.tasks[1]
    assert runs == [True, False, False, False, False]
    assert delays == [5, 30, 120, 120]
    account = await supervisor.storage.get(1)
    assert account.state == "error"
    assert account.state_reason == "ValueError: simulated transport failure"
    await supervisor.close()


async def test_api_bearer_validation_crud_and_account_isolation(storage, tmp_path):
    _, sessions, crypto = storage
    supervisor = Supervisor(sessions, crypto, NS(data_dir=tmp_path))
    bot = NS(
        get_me=AsyncMock(return_value=NS(id=555, has_topics_enabled=True)),
        session=NS(close=AsyncMock()),
    )
    api = InternalApi(supervisor, "fake-internal-token", bot_factory=lambda _: bot)
    request = make_mocked_request("GET", "/accounts")
    with pytest.raises(web.HTTPUnauthorized):
        await api.authenticate(request, api.accounts)
    request = make_mocked_request(
        "GET", "/accounts", headers={"Authorization": "Bearer fake-internal-token"}
    )
    response = await api.authenticate(request, api.accounts)
    accounts = json.loads(response.text)
    assert len(accounts) == 2
    assert all("tg_bot_token_enc" not in row for row in accounts)
    create = NS(
        json=AsyncMock(
            return_value={
                "name": "new",
                "phone": "+1234567890",
                "owner_tg_user_id": 5,
                "extra_owner_tg_user_ids": [6, 6, 7],
                "tg_bot_token": "fake-new-token",
            }
        )
    )
    response = await api.errors(create, api.create)
    assert response.status == 201
    account_id = json.loads(response.text)["id"]
    account = await supervisor.storage.get(account_id)
    assert crypto.decrypt(account.tg_bot_token_enc) == "fake-new-token"
    assert account.extra_owner_tg_user_ids == [6, 7]
    assert account.owner_ids == [5, 6, 7]
    bot.session.close.assert_awaited_once()
    patch = NS(match_info={"id": str(account_id)}, json=AsyncMock(return_value={"name": "renamed"}))
    assert (await api.errors(patch, api.patch)).status == 200
    owners = NS(
        match_info={"id": str(account_id)},
        json=AsyncMock(return_value={"extra_owner_tg_user_ids": [8]}),
    )
    assert json.loads((await api.errors(owners, api.patch)).text)["extra_owner_tg_user_ids"] == [8]
    invalid = NS(
        match_info={"id": str(account_id)}, json=AsyncMock(return_value={"state": "active"})
    )
    assert (await api.errors(invalid, api.patch)).status == 400
    store = RelayStorage(2, sessions)
    link = await store.ensure_chat(10, "CHAT", "Other", 0)
    wrong = NS(match_info={"id": "1", "link_id": str(link.id), "action": "mute"})
    with pytest.raises(web.HTTPNotFound):
        await api.mute(wrong)
    await api.delete(NS(match_info={"id": str(account_id)}))
    assert await supervisor.storage.get(account_id) is None
    await supervisor.close()


class RunnerMax:
    me_id = 1

    def __init__(self):
        self.sms, self.password = SmsCodeProvider(), PasswordProvider()
        self.handlers = {}
        self.fetch_chats = AsyncMock(return_value=[])
        self.task = None
        self.fail = None

    def on(self, name, handler):
        self.handlers[name] = handler

    def start(self):
        async def run():
            if self.fail:
                raise self.fail
            await self.handlers["start"](self)
            await asyncio.Future()

        self.task = asyncio.create_task(run())
        return self.task

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


class RunnerTg:
    def __init__(self, *args):
        self.stopped = asyncio.Event()
        self.dispatcher = NS(stop_polling=AsyncMock(side_effect=self.stopped.set))
        self.note = AsyncMock()
        self.on_polling_started = None

    async def start(self):
        await self.on_polling_started()
        await self.stopped.wait()

    async def stop(self):
        self.stopped.set()


async def test_runner_start_polling_catchup_and_clean_stop(storage, tmp_path):
    _, sessions, crypto = storage
    account = await AccountStorage(sessions).get(1)
    max_client = RunnerMax()
    tg = RunnerTg()
    settings = NS(data_dir=tmp_path, max_app_version=None, shutdown_timeout=0.1)
    runner = AccountRunner(
        account,
        sessions,
        crypto,
        settings,
        max_factory=AsyncMock(return_value=max_client),
        tg_factory=lambda *args: tg,
    )
    task = asyncio.create_task(runner.run())
    await asyncio.wait_for(runner.active.wait(), 1)
    assert (await AccountStorage(sessions).get(1)).state == "active"
    max_client.fetch_chats.assert_awaited_once()
    await max_client.handlers["start"](max_client)
    assert max_client.fetch_chats.await_count == 2
    polling = runner.polling_task
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert polling.done()
    assert max_client.task.done()
    assert tg.stopped.is_set()


async def test_runner_session_lost_no_sms_and_note(storage, tmp_path):
    _, sessions, crypto = storage
    account = await AccountStorage(sessions).get(1)
    account.inbox_chat_id = 1
    max_client = RunnerMax()
    max_client.fail = SessionLost("revoked")
    tg = RunnerTg()
    settings = NS(data_dir=tmp_path, max_app_version=None, shutdown_timeout=0.1)
    runner = AccountRunner(
        account,
        sessions,
        crypto,
        settings,
        max_factory=AsyncMock(return_value=max_client),
        tg_factory=lambda *args: tg,
    )
    with pytest.raises(SessionLost):
        await runner.run()
    assert (await AccountStorage(sessions).get(1)).state == "session_lost"
    assert not max_client.sms.requested.is_set()
    tg.note.assert_awaited_once()


async def test_guarded_pymax_translates_revocation(monkeypatch):
    from pymax import Client
    from pymax.exceptions import ApiError

    app = NS(start=AsyncMock(side_effect=ApiError(opcode=19, error="FAIL_LOGIN_TOKEN")))
    app.api = NS(
        uploads=NS(
            app=app, file_upload_waiters={}, video_upload_waiters={}, voice_upload_waiters={}
        )
    )
    monkeypatch.setattr(Client, "_build_app", lambda _: app)
    client = GateClient.__new__(GateClient)
    with pytest.raises(SessionLost):
        await client._build_app().start()


async def test_pause_logout_and_credentials(storage, tmp_path):
    from pymax.session import SessionInfo

    _, sessions, crypto = storage
    settings = Settings(_env_file=None, secret_key=crypto.generate_key(), data_dir=tmp_path)
    supervisor = Supervisor(sessions, crypto, settings)
    await SessionStore(1, sessions, crypto).save_session(
        SessionInfo(token="fake", phone="+1001", device_id="dev")
    )
    await supervisor.action(1, "pause")
    assert (await supervisor.storage.get(1)).state == "paused"
    await supervisor.action(1, "logout")
    async with sessions() as session:
        assert await session.get(MaxSession, 1) is None
    assert (await supervisor.storage.get(1)).state == "new"
    with pytest.raises(ValueError):
        await supervisor.provide(1, "code", "fake-code")
    with pytest.raises(ValueError):
        await supervisor.action(1, "resume")
    await supervisor.close()


@pytest.mark.parametrize("concurrent", [False, True])
async def test_duplicate_bot_accounts_conflict(storage, tmp_path, concurrent):
    _, sessions, crypto = storage
    supervisor = Supervisor(sessions, crypto, NS(data_dir=tmp_path))
    bot = NS(
        get_me=AsyncMock(return_value=NS(id=777, has_topics_enabled=True)),
        session=NS(close=AsyncMock()),
    )
    api = InternalApi(supervisor, "fake", bot_factory=lambda _: bot)
    requests = [
        NS(
            json=AsyncMock(
                return_value=dict(
                    name=f"account {n}",
                    phone=f"+123456789{n}",
                    owner_tg_user_id=10 + n,
                    tg_bot_token="fictional-token",
                )
            )
        )
        for n in (1, 2)
    ]

    async def create(request):
        try:
            return (await api.create(request)).status
        except web.HTTPConflict as exc:
            assert "already assigned" in exc.text
            return exc.status

    statuses = (
        await asyncio.gather(*(create(r) for r in requests))
        if concurrent
        else [await create(r) for r in requests]
    )
    assert sorted(statuses) == [201, 409]
    assert sum(a.tg_bot_id == 777 for a in await supervisor.storage.all()) == 1
    await supervisor.close()


async def test_extra_owners_patch_keeps_inbox(storage, tmp_path):
    _, sessions, crypto = storage
    supervisor = Supervisor(sessions, crypto, NS(data_dir=tmp_path))
    await supervisor.storage.update(1, inbox_chat_id=100)
    store = RelayStorage(1, sessions)
    link = await store.ensure_chat(10, "CHAT", "Title", 0)
    await store.change_chat(link.id, topic_id=200)
    await supervisor.patch(1, {"extra_owner_tg_user_ids": [77]})
    account = await supervisor.storage.get(1)
    assert account.inbox_chat_id == 100
    assert account.owner_ids == [1, 77]
    assert (await store.chat(link_id=link.id)).topic_id == 200
    await supervisor.close()


async def test_inbox_patch_clears_topics_and_message_links(storage, tmp_path):
    _, sessions, crypto = storage
    supervisor = Supervisor(sessions, crypto, NS(data_dir=tmp_path))
    await supervisor.storage.update(1, inbox_chat_id=100)
    store = RelayStorage(1, sessions)
    link = await store.ensure_chat(10, "CHAT", "Title", 0)
    await store.change_chat(link.id, topic_id=200, renamed_by_owner=True)
    await store.link_messages(link.id, 1, [100], "max_to_tg")
    await supervisor.patch(1, {"inbox_mode": "supergroup"})
    assert (await supervisor.storage.get(1)).inbox_chat_id is None
    current = await store.chat(link_id=link.id)
    assert current.topic_id is None and not current.renamed_by_owner
    assert await store.messages(link.id) == []
    assert await store.chat(topic_id=200) is None
    await supervisor.close()


@pytest.mark.parametrize(
    "kind,value,expected",
    [
        ("password", "  password  ", "  password  "),
        ("password", "  ", "  "),
        ("code", " 123456 ", "123456"),
    ],
)
async def test_runner_credentials_are_exact(storage, tmp_path, kind, value, expected):
    _, sessions, crypto = storage
    runner = AccountRunner(
        await AccountStorage(sessions).get(1), sessions, crypto, NS(data_dir=tmp_path)
    )
    runner.max = RunnerMax()
    provider = runner.max.password if kind == "password" else runner.max.sms
    provider.requested.set()
    await runner.provide(kind, value)
    assert provider.queue.get_nowait() == expected


async def test_topic_endpoint_bound_inbox_idempotency_and_scope(storage, tmp_path):
    from maxgate.db.models import Account
    from maxgate.tg.bot import TgBot
    from maxgate.tg.topics import ensure_topic

    _, sessions, crypto = storage
    supervisor = Supervisor(sessions, crypto, NS(data_dir=tmp_path))
    store = RelayStorage(1, sessions)
    link = await store.ensure_chat(10, "CHAT", "Group", 0)
    bot = NS(
        create_forum_topic=AsyncMock(return_value=NS(message_thread_id=77)),
        session=NS(close=AsyncMock()),
    )
    api = InternalApi(supervisor, "fake", bot_factory=lambda _: bot)
    req = NS(match_info={"id": "1", "link_id": str(link.id)})
    with pytest.raises(web.HTTPPreconditionFailed):
        await api.topic(req)
    bot.create_forum_topic.assert_not_called()
    async with sessions.begin() as session:
        account = await session.get(Account, 1)
        account.inbox_chat_id = 1
    # Operator requests and automatic creation use the same lock and implementation.
    tg = TgBot("", account, sessions, bot=bot)
    results = await asyncio.gather(
        api.topic(req), api.topic(req), ensure_topic(store, tg, link.id, supervisor.topic_locks)
    )
    assert json.loads(results[0].text)["topic_id"] == 77
    assert json.loads(results[1].text)["topic_id"] == 77
    assert results[2].topic_id == 77
    bot.create_forum_topic.assert_awaited_once_with(chat_id=1, name="Group", icon_color=0x6FB9F0)
    with pytest.raises(web.HTTPNotFound):
        await api.topic(NS(match_info={"id": "2", "link_id": str(link.id)}))
    assert any(
        str(r.resource) == "<DynamicResource  /accounts/{id}/chats/{link_id}/topic>"
        for r in api.app.router.routes()
    )
    await supervisor.close()


@pytest.mark.parametrize(
    "case", ["success", "unchanged", "no_topic", "telegram_error", "wrong_account", "empty", "long"]
)
async def test_rename_topic_endpoint(storage, tmp_path, case):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditForumTopic
    from aiohttp.test_utils import TestClient, TestServer

    _, sessions, crypto = storage
    supervisor = Supervisor(sessions, crypto, NS(data_dir=tmp_path))
    await supervisor.storage.update(1, inbox_chat_id=100)
    store = RelayStorage(1, sessions)
    link = await store.ensure_chat(10, "CHAT", "MAX title", 0)
    if case != "no_topic":
        await store.change_chat(link.id, topic_id=77)
    bot = NS(edit_forum_topic=AsyncMock(), session=NS(close=AsyncMock()))
    if case in {"telegram_error", "unchanged"}:
        bot.edit_forum_topic.side_effect = TelegramBadRequest(
            method=EditForumTopic(chat_id=100, message_thread_id=77),
            message="Bad Request: TOPIC_NOT_MODIFIED" if case == "unchanged" else "secret-token",
        )
    api = InternalApi(supervisor, "fake", bot_factory=lambda _: bot)
    account_id = 2 if case == "wrong_account" else 1
    title = "   " if case == "empty" else "x" * 129 if case == "long" else "  Моё название  "
    async with TestClient(TestServer(api.app)) as client:
        response = await client.patch(
            f"/accounts/{account_id}/chats/{link.id}/topic",
            json={"name": title},
            headers={"Authorization": "Bearer fake"},
        )
        expected = {
            "success": 200,
            "unchanged": 200,
            "no_topic": 400,
            "telegram_error": 502,
            "wrong_account": 404,
            "empty": 400,
            "long": 400,
        }[case]
        assert response.status == expected
        assert "secret-token" not in await response.text()
        response = await client.get("/accounts/1/chats", headers={"Authorization": "Bearer fake"})
        updated = (await response.json())[0]
        assert updated["renamed_by_owner"] == (case in {"success", "unchanged"})
        assert updated["max_title"] == "MAX title"
        assert updated["topic_title"] == (
            "Моё название" if case in {"success", "unchanged"} else None
        )
    if case in {"success", "unchanged"}:
        bot.edit_forum_topic.assert_awaited_once_with(
            chat_id=100, message_thread_id=77, name="Моё название"
        )
    elif case != "telegram_error":
        bot.edit_forum_topic.assert_not_awaited()
    await supervisor.close()
    if case in {"success", "unchanged"}:
        from maxgate.db import create_storage

        engine, _sessions, _crypto = storage
        fresh_engine, fresh_sessions = create_storage(str(engine.url))
        try:
            persisted = await RelayStorage(1, fresh_sessions).chat(link_id=link.id)
            assert persisted.topic_title == "Моё название"
        finally:
            await fresh_engine.dispose()


async def test_ui_rename_serializes_with_max_title_change_and_service_event(storage, tmp_path):
    from datetime import UTC, datetime

    from aiogram.types import Message

    from maxgate.relay.engine import RelayEngine
    from maxgate.relay.queue import ChatQueues
    from maxgate.tg.bot import TgBot

    _, sessions, crypto = storage
    supervisor = Supervisor(sessions, crypto, NS(data_dir=tmp_path))
    await supervisor.storage.update(1, inbox_chat_id=100)
    account = await supervisor.storage.get(1)
    store = RelayStorage(1, sessions)
    link = await store.ensure_chat(10, "CHAT", "Old", 0)
    await store.change_chat(link.id, topic_id=77)
    entered, release = asyncio.Event(), asyncio.Event()
    titles = []

    async def edit(**kwargs):
        titles.append(kwargs["name"])
        if kwargs["name"] == "Mine":
            entered.set()
            await release.wait()

    bot = NS(edit_forum_topic=AsyncMock(side_effect=edit), session=NS(close=AsyncMock()))
    tg = TgBot("", account, sessions, bot=bot)
    relay = RelayEngine(
        account,
        sessions,
        NS(),
        tg,
        tmp_path,
        AsyncMock(),
        queues=ChatQueues(sleep=lambda _: asyncio.sleep(0)),
    )
    relay.topic_locks = supervisor.topic_locks
    tg.on_topic_edited = relay.topic_edited
    api = InternalApi(supervisor, "fake", bot_factory=lambda _: bot)
    req = NS(
        match_info={"id": "1", "link_id": str(link.id)},
        json=AsyncMock(return_value={"name": "Mine"}),
    )
    task = asyncio.create_task(api.rename_topic(req))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await relay.chat_update(NS(id=10, title="New MAX title"))
        # Drain a queued MAX update while the UI call is still awaiting Telegram.
        try:
            await asyncio.wait_for(asyncio.shield(relay.queues.drain()), 0.1)
        except TimeoutError:
            pass
        release.set()
        assert (await task).status == 200
        await relay.queues.drain()
        assert titles == ["Mine"]
        update = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat={"id": 100, "type": "private"},
            message_thread_id=77,
            forum_topic_edited={"name": "Mine"},
        )
        await tg._message(update)
        assert (await store.chat(link_id=link.id)).renamed_by_owner
        assert (await store.chat(link_id=link.id)).max_title == "New MAX title"
        # Automatic edits are still ignored; an Owner's subsequent edit is recognized.
        await store.change_chat(link.id, renamed_by_owner=False)
        await tg.edit_topic(77, "Automatic")
        await tg._message(
            update.model_copy(
                update={
                    "forum_topic_edited": update.forum_topic_edited.model_copy(
                        update={"name": "Automatic"}
                    )
                }
            )
        )
        assert not (await store.chat(link_id=link.id)).renamed_by_owner
        await tg._message(update)
        assert (await store.chat(link_id=link.id)).renamed_by_owner
    finally:
        release.set()
        await task
        await relay.close(timeout=0.2)
        await supervisor.close()
