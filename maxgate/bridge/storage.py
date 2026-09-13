import logging

from sqlalchemy import delete, select

from maxgate.db.models import Account, AccountEvent

logger = logging.getLogger("maxgate")


class AccountStorage:
    def __init__(self, sessions):
        self.sessions = sessions

    async def get(self, account_id):
        async with self.sessions() as session:
            return await session.get(Account, account_id)

    async def all(self):
        async with self.sessions() as session:
            return list(await session.scalars(select(Account).order_by(Account.id)))

    async def update(self, account_id, **changes):
        async with self.sessions.begin() as session:
            account = await session.get(Account, account_id)
            if account is None:
                raise ValueError("Account not found")
            for key, value in changes.items():
                setattr(account, key, value)
        return account

    async def event(self, account_id, message, level="INFO"):
        # Вызывающий передаёт только фиксированный текст/безопасный код ошибки.
        logger.log(getattr(logging, level), "Account %s: %s", account_id, message)
        async with self.sessions.begin() as session:
            session.add(AccountEvent(account_id=account_id, level=level, message=message))
            await session.flush()
            keep = (
                select(AccountEvent.id)
                .where(AccountEvent.account_id == account_id)
                .order_by(AccountEvent.id.desc())
                .limit(1000)
            )
            await session.execute(
                delete(AccountEvent).where(
                    AccountEvent.account_id == account_id, AccountEvent.id.not_in(keep)
                )
            )

    async def state(self, account_id, state, reason=None):
        await self.update(account_id, state=state, state_reason=reason)
        await self.event(account_id, f"state={state}" + (f" ({reason})" if reason else ""))
