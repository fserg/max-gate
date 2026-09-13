import pytest_asyncio

from maxgate.crypto import Crypto
from maxgate.db import create_storage
from maxgate.db.models import Account, Base


@pytest_asyncio.fixture
async def storage(tmp_path):
    engine, sessions = create_storage(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    crypto = Crypto(Crypto.generate_key())
    async with sessions.begin() as session:
        for n in (1, 2):
            session.add(
                Account(
                    id=n,
                    name=f"Account {n}",
                    phone=f"+100{n}",
                    owner_tg_user_id=n,
                    tg_bot_token_enc=crypto.encrypt("fake-bot"),
                )
            )
    yield engine, sessions, crypto
    await engine.dispose()
