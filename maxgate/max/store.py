import asyncio

from pymax.session import SessionInfo
from sqlalchemy.dialects.sqlite import insert

from maxgate.db.models import MaxSession, utcnow


class SessionStore:
    """StoreProtocol одного Account. Engine принадлежит Gate, close его не закрывает."""

    def __init__(self, account_id, session_factory, crypto):
        self.account_id = account_id
        self._sessions = session_factory
        self._crypto = crypto
        self._lock = asyncio.Lock()

    async def save_session(self, session_info: SessionInfo) -> None:
        data = dict(
            account_id=self.account_id,
            token_enc=self._crypto.encrypt(session_info.token),
            device_id=session_info.device_id,
            phone=session_info.phone,
            mt_instance_id=session_info.mt_instance_id,
            user_agent_json=(
                session_info.user_agent.model_dump(mode="json") if session_info.user_agent else None
            ),
            sync_json=session_info.sync.model_dump(mode="json"),
            updated_at=utcnow(),
        )
        async with self._lock, self._sessions.begin() as session:
            await session.execute(
                insert(MaxSession)
                .values(**data)
                .on_conflict_do_update(index_elements=["account_id"], set_=data)
            )

    async def load_session(self) -> SessionInfo | None:
        async with self._sessions() as session:
            row = await session.get(MaxSession, self.account_id)
            if row is None:
                return None
            return SessionInfo(
                token=self._crypto.decrypt(row.token_enc),
                device_id=row.device_id,
                phone=row.phone,
                mt_instance_id=row.mt_instance_id,
                user_agent=row.user_agent_json,
                sync=row.sync_json,
            )

    async def load_session_by_device_id(self, device_id: str) -> SessionInfo | None:
        info = await self.load_session()
        return info if info and info.device_id == device_id else None

    async def load_session_by_phone(self, phone: str) -> SessionInfo | None:
        info = await self.load_session()
        return info if info and info.phone == phone else None

    async def update_token(self, old_token: str, new_token: str, /) -> None:
        async with self._lock, self._sessions.begin() as session:
            row = await session.get(MaxSession, self.account_id)
            if row and self._crypto.decrypt(row.token_enc) == old_token:
                row.token_enc = self._crypto.encrypt(new_token)

    async def delete_session(self, token: str, /) -> None:
        async with self._lock, self._sessions.begin() as session:
            row = await session.get(MaxSession, self.account_id)
            if row and self._crypto.decrypt(row.token_enc) == token:
                await session.delete(row)

    async def close(self) -> None:
        pass
