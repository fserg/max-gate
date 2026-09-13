import json
import sqlite3

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from cryptography.fernet import InvalidToken
from pymax.config import ExtraConfig
from sqlalchemy import create_engine, select

from maxgate.cli import import_session, migration_config, seed_account
from maxgate.config import Settings
from maxgate.crypto import Crypto
from maxgate.db import create_storage
from maxgate.db.models import Account, Base, MaxSession
from maxgate.max.store import SessionStore


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        secret_key=Crypto.generate_key(),
        data_dir=tmp_path,
        TEL="+1000",
        TG_BOT_TOKEN="123:fake-token",
    )


def test_migration_upgrade_downgrade(settings):
    config = migration_config(settings)
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{settings.data_dir / 'maxgate.db'}")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()
    command.downgrade(config, "base")
    command.upgrade(config, "head")


async def test_seed_import_is_atomic_and_idempotent(settings):
    engine, sessions = create_storage(settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    source = settings.data_dir / "source.db"
    agent = ExtraConfig().generate_user_agent("26.31.0", 1)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE TABLE sessions(token TEXT, device_id TEXT, phone TEXT, "
            "mt_instance_id TEXT, chats_sync INTEGER, contacts_sync INTEGER, drafts_sync INTEGER, "
            "presence_sync INTEGER, config_hash TEXT, user_agent TEXT)"
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "fake-session",
                "device",
                "+1000",
                "instance",
                1,
                2,
                3,
                4,
                "hash",
                json.dumps(agent.model_dump(mode="json", by_alias=True)),
            ),
        )
    info = import_session(source, "+1000")
    assert info.user_agent == agent
    assert info.sync.presence_sync == 4
    account_id = await seed_account(settings, 123, source)
    store = SessionStore(account_id, sessions, Crypto(settings.secret_key))
    assert await store.load_session() == info
    await store.update_token("fake-session", "rotated-session")
    assert await seed_account(settings, 123, source) == account_id
    assert (await store.load_session()).token == "rotated-session"
    async with sessions() as session:
        accounts = (await session.scalars(select(Account))).all()
        assert len(accounts) == 1
        assert accounts[0].tg_bot_id == 123
        row = await session.get(MaxSession, account_id)
        assert row.user_agent_json == agent.model_dump(mode="json")
        assert row.token_enc != "rotated-session"
        with pytest.raises(InvalidToken):
            Crypto(Crypto.generate_key()).decrypt(row.token_enc)
    await engine.dispose()


def test_existing_accounts_migration_preserves_relations(settings):
    config = migration_config(settings)
    command.upgrade(config, "0001")
    crypto = Crypto(settings.secret_key)
    path = settings.data_dir / "maxgate.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO accounts VALUES (1,'dev','+1000',?,1,'private',10,0,'paused',NULL,?,?)",
            (crypto.encrypt("123:fake-token"), "2026-01-01", "2026-01-01"),
        )
        db.execute(
            "INSERT INTO max_sessions VALUES (1,?,'device','+1000','','{}','{}',?)",
            (crypto.encrypt("saved-session"), "2026-01-01"),
        )
        db.execute(
            "INSERT INTO chat_links VALUES (1,1,10,'CHAT','title',20,0,0,1000,?)", ("2026-01-01",)
        )
        db.execute(
            "INSERT INTO message_links VALUES (1,1,1,30,40,0,'max_to_tg',?)", ("2026-01-01",)
        )
    command.upgrade(config, "head")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT tg_bot_id FROM accounts").fetchone() == (123,)
        for table in ("max_sessions", "chat_links", "message_links"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone() == (1,)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE accounts SET state='invalid'")
    command.downgrade(config, "0001")
    command.upgrade(config, "head")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM max_sessions").fetchone() == (1,)


def test_duplicate_legacy_bots_abort_migration_without_changes(settings):
    config = migration_config(settings)
    command.upgrade(config, "0001")
    crypto = Crypto(settings.secret_key)
    path = settings.data_dir / "maxgate.db"
    with sqlite3.connect(path) as db:
        for n in (1, 2):
            db.execute(
                "INSERT INTO accounts VALUES (?,'dev','+1000',?,1,'private',10,0,'paused',NULL,?,?)",
                (n, crypto.encrypt("123:fake-token"), "2026-01-01", "2026-01-01"),
            )
    with pytest.raises(ValueError, match="Duplicate Telegram bots"):
        command.upgrade(config, "head")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0001",)
        assert "tg_bot_id" not in [r[1] for r in db.execute("PRAGMA table_info(accounts)")]
        assert db.execute("SELECT count(*) FROM accounts").fetchone() == (2,)
