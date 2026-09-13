import asyncio
from contextlib import suppress

from maxgate.bridge.storage import AccountStorage
from maxgate.max.client import MaxClient, SessionLost, is_session_lost
from maxgate.max.store import SessionStore
from maxgate.relay.engine import RelayEngine
from maxgate.relay.errors import reason
from maxgate.tg.bot import TgBot


class AccountRunner:
    def __init__(
        self,
        account,
        sessions,
        crypto,
        settings,
        *,
        login=False,
        max_factory=MaxClient.create,
        tg_factory=TgBot,
    ):
        self.account, self.sessions = account, sessions
        self.crypto, self.settings, self.login = crypto, settings, login
        self.max_factory, self.tg_factory = max_factory, tg_factory
        self.storage = AccountStorage(sessions)
        self.max = self.tg = self.relay = None
        self.polling_task = None
        self.active = asyncio.Event()
        self.stopping = False
        self.failures: asyncio.Queue[Exception] = asyncio.Queue()
        self.started_at = None

    async def event(self, message, level="INFO"):
        await self.storage.event(self.account.id, message, level)

    async def state(self, state, reason=None):
        self.account.state, self.account.state_reason = state, reason
        await self.storage.state(self.account.id, state, reason)

    async def _sms_requested(self):
        await self.state("logging_in")
        await self.event("SMS code requested; awaiting Operator (60 s)")

    async def _password_requested(self):
        await self.state("password_required")

    async def _polling_started(self):
        await self.event("Telegram polling started")
        await self.state("active")
        self.started_at = asyncio.get_running_loop().time()
        self.active.set()

    async def _max_start(self, _):
        if self.stopping:
            return
        await self.event(
            "MAX login successful; saved Session" if not self.login else "MAX login successful"
        )
        await self.relay.catch_up()
        if self.polling_task is None:
            self.polling_task = asyncio.create_task(self.tg.start())
        else:
            await self.state("active")
        await self.event("Catch-up scheduled")

    async def _error(self, exc, _):
        self.failures.put_nowait(
            SessionLost("Session MAX revoked") if is_session_lost(exc) else exc
        )

    async def _disconnect(self, exc, reconnect, delay):
        if self.stopping:
            return
        if is_session_lost(exc):
            self.failures.put_nowait(SessionLost("Session MAX revoked"))
        else:
            await self.event("MAX disconnected; reconnect scheduled", "WARNING")

    async def run(self):
        failure_task = None
        ready_task = None
        try:
            store = SessionStore(self.account.id, self.sessions, self.crypto)
            self.max = await self.max_factory(
                self.account.phone,
                store,
                self.settings.data_dir,
                self.settings.max_app_version,
                saved_session_only=not self.login,
            )
            self.tg = self.tg_factory(
                self.crypto.decrypt(self.account.tg_bot_token_enc), self.account, self.sessions
            )
            self.relay = RelayEngine(
                self.account, self.sessions, self.max, self.tg, self.settings.data_dir, self.event
            )
            self.max.sms.on_requested = self._sms_requested
            self.max.password.on_requested = self._password_requested
            self.tg.on_message = self.relay.accept_tg
            self.tg.on_edit = self.relay.tg_edit
            self.tg.on_topic_edited = self.relay.topic_edited
            self.tg.on_inbox_bound = self.relay.inbox_bound
            self.tg.on_command = self.relay.command
            self.tg.on_polling_started = self._polling_started
            self.max.on("start", self._max_start)
            self.max.on("error", self._error)
            self.max.on("disconnect", self._disconnect)
            for event, method in (
                ("message", self.relay.accept_max),
                ("message_edit", self.relay.max_edit),
                ("message_delete", self.relay.max_delete),
                ("chat_update", self.relay.chat_update),
            ):

                async def callback(data, _, method=method):
                    if not self.stopping:
                        await method(data)

                self.max.on(event, callback)
            await self.state("logging_in")
            max_task = self.max.start()
            failure_task = asyncio.create_task(self.failures.get())
            ready_task = asyncio.create_task(self._await_polling())
            done, _ = await asyncio.wait(
                [max_task, failure_task, ready_task], return_when=asyncio.FIRST_COMPLETED
            )
            if failure_task in done:
                raise failure_task.result()
            if max_task in done:
                await max_task
                raise RuntimeError("MAX client stopped")
            await ready_task
            done, _ = await asyncio.wait(
                [max_task, self.polling_task, failure_task], return_when=asyncio.FIRST_COMPLETED
            )
            if failure_task in done:
                raise failure_task.result()
            for task in done:
                await task
            raise RuntimeError("Account client stopped unexpectedly")
        except SessionLost:
            await self.state("session_lost", "Session MAX отозвана")
            if self.tg and self.account.inbox_chat_id is not None:
                try:
                    await self.tg.note(
                        "ℹ️ Session MAX отозвана. Operator должен выполнить вход заново."
                    )
                except Exception as exc:
                    await self.event(f"Session Note failed: {reason(exc)}", "ERROR")
            raise
        finally:
            for task in (failure_task, ready_task):
                if task:
                    task.cancel()
            await asyncio.gather(
                *(t for t in (failure_task, ready_task) if t), return_exceptions=True
            )
            await self.close()

    async def _await_polling(self):
        # Создание polling_task происходит внутри on_start; active устанавливается после getMe.
        async with asyncio.timeout(180):
            while not self.active.is_set():
                if self.polling_task and self.polling_task.done():
                    await self.polling_task
                    raise RuntimeError("Telegram polling stopped before startup")
                await asyncio.sleep(0.05)

    async def provide(self, kind, value):
        if self.max is None:
            raise ValueError("Account login is not ready")
        provider = self.max.sms if kind == "code" else self.max.password
        if not provider.requested.is_set():
            raise ValueError("Account is not awaiting this credential")
        value = value.strip() if kind == "code" else value
        if not value:
            raise ValueError("Credential cannot be empty")
        provider.queue.put_nowait(value)

    async def close(self):
        if self.stopping:
            return
        self.stopping = True
        # Сначала прекращаем приём заданий; соединения доступны для дренирования Relay.
        if self.relay:
            self.relay.closed = True
        if self.polling_task and not self.polling_task.done():
            with suppress(RuntimeError):
                await self.tg.dispatcher.stop_polling()
        lost = 0
        if self.relay:
            lost = await self.relay.close(self.settings.shutdown_timeout)
        if self.max:
            with suppress(Exception):
                await self.max.stop()
        if self.polling_task:
            self.polling_task.cancel()
            await asyncio.gather(self.polling_task, return_exceptions=True)
        if self.tg:
            await self.tg.stop()
        await self.event(
            f"AccountRunner stopped with {lost} undelivered jobs"
            if lost
            else "AccountRunner stopped cleanly",
            "WARNING" if lost else "INFO",
        )
