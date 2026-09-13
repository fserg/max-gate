from pymax.session import SessionInfo, StoreProtocol
from sqlalchemy import delete

from maxgate.db.models import Account, MaxSession
from maxgate.max.store import SessionStore


async def test_session_roundtrip_rotation_isolation(storage):
    _, sessions, crypto = storage
    first, second = [SessionStore(n, sessions, crypto) for n in (1, 2)]
    assert isinstance(first, StoreProtocol)
    info = SessionInfo(
        token="fake-old",
        device_id="device",
        phone="+1001",
        mt_instance_id="instance",
        sync={"chats_sync": 123, "config_hash": "hash"},
    )
    assert await first.load_session() is None
    await first.save_session(info)
    await second.save_session(info)
    assert await first.load_session() == info
    async with sessions() as session:
        row = await session.get(MaxSession, 1)
        assert "fake-old" not in row.token_enc
        assert crypto.decrypt(row.token_enc) == "fake-old"
    assert await first.load_session_by_phone("+1002") is None
    assert await first.load_session_by_device_id("wrong") is None
    assert await first.load_session_by_phone("+1001") == info
    assert await first.load_session_by_device_id("device") == info
    await first.update_token("wrong", "unused")
    assert await first.load_session() == info
    await first.update_token("fake-old", "fake-new")
    assert (await first.load_session()).token == "fake-new"
    assert (await second.load_session()).token == "fake-old"
    await first.delete_session("fake-old")
    assert await first.load_session() is not None
    await first.close()
    assert await first.load_session() is not None
    await first.delete_session("fake-new")
    assert await first.load_session() is None
    assert await second.load_session() is not None


async def test_session_replace_and_account_cascade(storage):
    _, sessions, crypto = storage
    store = SessionStore(1, sessions, crypto)
    original = SessionInfo(token="fake", device_id="one", phone="+1001")
    await store.save_session(original)
    await store.save_session(original.model_copy(update={"device_id": "two"}))
    assert (await store.load_session()).device_id == "two"
    async with sessions.begin() as session:
        await session.execute(delete(Account).where(Account.id == 1))
    assert await store.load_session() is None
