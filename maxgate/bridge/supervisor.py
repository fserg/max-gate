import asyncio
from contextlib import suppress

from sqlalchemy import delete, select

from maxgate.bridge.runner import AccountRunner
from maxgate.bridge.storage import AccountStorage
from maxgate.db.models import Account, MaxSession
from maxgate.max.client import SessionLost
from maxgate.relay.errors import reason
from maxgate.relay.storage import RelayStorage


class Supervisor:
    def __init__(
        self, sessions, crypto, settings, *, runner_factory=AccountRunner, sleep=asyncio.sleep
    ):
        self.sessions, self.crypto, self.settings = sessions, crypto, settings
        self.storage = AccountStorage(sessions)
        self.runner_factory, self.sleep = runner_factory, sleep
        self.runners, self.tasks = {}, {}
        self.locks = {}
        self.topic_locks = {}
        self.closed = False
        self.maintenance_task = None

    def lock(self, account_id):
        return self.locks.setdefault(account_id, asyncio.Lock())

    async def start(self):
        async with self.sessions() as session:
            ids = set(await session.scalars(select(MaxSession.account_id)))
        for account in await self.storage.all():
            if account.id in ids and account.state not in {"paused", "session_lost", "error"}:
                await self.launch(account.id)
        self.maintenance_task = asyncio.create_task(self._maintenance())

    async def launch(self, account_id, *, login=False):
        if self.closed:
            raise ValueError("Gate is stopping")
        await self._stop(account_id)
        if await self.storage.get(account_id) is None:
            raise ValueError("Account not found")
        self.tasks[account_id] = asyncio.create_task(self._supervise(account_id, login))

    async def _supervise(self, account_id, login):
        failures = 0
        while not self.closed:
            account = await self.storage.get(account_id)
            runner = self.runner_factory(
                account, self.sessions, self.crypto, self.settings, login=login
            )
            runner.topic_locks = self.topic_locks
            self.runners[account_id] = runner
            try:
                await runner.run()
                raise RuntimeError("AccountRunner exited")
            except asyncio.CancelledError:
                raise
            except SessionLost:
                return
            except Exception as exc:
                # Стабильный запуск разрывает серию быстрых неудач.
                if (
                    runner.started_at
                    and asyncio.get_running_loop().time() - runner.started_at >= 120
                ):
                    failures = 0
                failures += 1
                safe = reason(exc)
                await self.storage.event(
                    account_id, f"AccountRunner failed ({failures}/5): {safe}", "ERROR"
                )
                if failures >= 5:
                    await self.storage.state(account_id, "error", safe)
                    return  # новый SMS только по явной команде Operator
                login = False  # повторные старты не запрашивают новый SMS автоматически
                await self.sleep((5, 30, 120)[min(failures - 1, 2)])
            finally:
                self.runners.pop(account_id, None)

    async def _stop(self, account_id):
        task = self.tasks.pop(account_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.runners.pop(account_id, None)

    async def action(self, account_id, action):
        async with self.lock(account_id):
            account = await self.storage.get(account_id)
            if account is None:
                raise ValueError("Account not found")
            if action in {"login", "resume"}:
                if action == "resume":
                    async with self.sessions() as session:
                        if await session.get(MaxSession, account_id) is None:
                            raise ValueError("Saved Session required; use login")
                else:
                    await self._stop(account_id)
                    async with self.sessions.begin() as session:
                        await session.execute(
                            delete(MaxSession).where(MaxSession.account_id == account_id)
                        )
                await self.launch(account_id, login=action == "login")
            elif action in {"pause", "logout", "delete"}:
                await self._stop(account_id)
                if action in {"logout", "delete"}:
                    async with self.sessions.begin() as session:
                        if action == "delete":
                            await session.execute(delete(Account).where(Account.id == account_id))
                            return
                        await session.execute(
                            delete(MaxSession).where(MaxSession.account_id == account_id)
                        )
                await self.storage.state(account_id, "new" if action == "logout" else "paused")
            else:
                raise ValueError("Unknown Account action")

    async def provide(self, account_id, kind, value):
        async with self.lock(account_id):
            runner = self.runners.get(account_id)
            if runner is None:
                raise ValueError("AccountRunner is not running")
            await runner.provide(kind, value)

    async def patch(self, account_id, changes):
        async with self.lock(account_id):
            account = await self.storage.get(account_id)
            if account is None:
                raise ValueError("Account not found")
            if (
                changes.get("inbox_mode", account.inbox_mode) != account.inbox_mode
                or changes.get("owner_tg_user_id", account.owner_tg_user_id)
                != account.owner_tg_user_id
            ):
                changes["inbox_chat_id"] = None
                from sqlalchemy import update

                from maxgate.db.models import ChatLink, MessageLink

                await self._stop(account_id)
                async with self.sessions.begin() as session:
                    await session.execute(
                        update(ChatLink)
                        .where(ChatLink.account_id == account_id)
                        .values(topic_id=None, renamed_by_owner=False)
                    )
                    await session.execute(
                        delete(MessageLink).where(MessageLink.account_id == account_id)
                    )
                updated = await self.storage.update(account_id, **changes)
                if account.state == "active":
                    await self.launch(account_id)
                return updated
            updated = await self.storage.update(account_id, **changes)
            runner = self.runners.get(account_id)
            if runner:
                for key, value in changes.items():
                    setattr(runner.account, key, value)
            return updated

    async def _maintenance(self):
        while True:
            for account in await self.storage.all():
                await RelayStorage(account.id, self.sessions).cleanup()
            await self.sleep(86400)

    async def close(self):
        self.closed = True
        if self.maintenance_task:
            self.maintenance_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.maintenance_task
        await asyncio.gather(*(self._stop(key) for key in list(self.tasks)))
