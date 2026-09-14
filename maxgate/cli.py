import argparse
import asyncio
import json
import logging
import sqlite3
from pathlib import Path

from aiogram.utils.token import extract_bot_id
from alembic import command
from alembic.config import Config
from pymax.session import SessionInfo
from sqlalchemy import select

from maxgate.config import Settings
from maxgate.crypto import Crypto
from maxgate.db import create_storage
from maxgate.db.models import Account, MaxSession
from maxgate.max.client import MaxClient
from maxgate.max.store import SessionStore


def migration_config(settings: Settings) -> Config:
    config = Config()
    config.attributes["secret_key"] = settings.secret_key
    config.set_main_option("script_location", str(Path(__file__).parent / "db" / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def import_session(path: Path, phone: str) -> SessionInfo:
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM sessions WHERE phone = ?", (phone,)).fetchall()
    if len(rows) != 1:
        raise ValueError("Expected exactly one saved Session for dev Account")
    data = dict(rows[0])
    return SessionInfo(
        token=data["token"],
        device_id=data["device_id"],
        phone=data["phone"],
        mt_instance_id=data["mt_instance_id"] or "",
        user_agent=json.loads(data["user_agent"]) if data["user_agent"] else None,
        sync={
            key: data[key]
            for key in (
                "chats_sync",
                "contacts_sync",
                "drafts_sync",
                "presence_sync",
                "config_hash",
            )
            if data[key] is not None
        },
    )


async def seed_account(settings: Settings, owner: int, source: Path) -> int:
    if not settings.tel or not settings.tg_bot_token:
        raise ValueError("TEL and TG_BOT_TOKEN are required")
    phone = settings.tel.get_secret_value()
    bot_id = extract_bot_id(settings.tg_bot_token.get_secret_value())
    crypto = Crypto(settings.secret_key)
    engine, sessions = create_storage(settings.database_url)
    try:
        async with sessions.begin() as session:
            accounts = (
                await session.scalars(
                    select(Account).where(Account.phone == phone, Account.name == "dev")
                )
            ).all()
            if len(accounts) > 1:
                raise ValueError("Multiple dev Accounts found")
            account = accounts[0] if accounts else None
            if account is None:
                account = Account(
                    name="dev",
                    tg_bot_id=bot_id,
                    phone=phone,
                    owner_tg_user_id=owner,
                    tg_bot_token_enc=crypto.encrypt(settings.tg_bot_token.get_secret_value()),
                )
                session.add(account)
                await session.flush()
            else:
                account.tg_bot_id = bot_id
                account.owner_tg_user_id = owner
                account.tg_bot_token_enc = crypto.encrypt(settings.tg_bot_token.get_secret_value())
            # Не заменяем обновлённый сервером токен устаревшим токеном из spike.
            if await session.get(MaxSession, account.id) is None:
                info = import_session(source, phone)
                session.add(
                    MaxSession(
                        account_id=account.id,
                        token_enc=crypto.encrypt(info.token),
                        device_id=info.device_id,
                        phone=info.phone,
                        mt_instance_id=info.mt_instance_id,
                        user_agent_json=info.user_agent.model_dump(mode="json")
                        if info.user_agent
                        else None,
                        sync_json=info.sync.model_dump(mode="json"),
                    )
                )
            return account.id
    finally:
        await engine.dispose()


async def max_check(settings: Settings, account_id: int):
    engine, sessions = create_storage(settings.database_url)
    adapter = None
    try:
        async with sessions() as session:
            account = await session.get(Account, account_id)
        if account is None:
            raise ValueError("Account not found")
        store = SessionStore(account_id, sessions, Crypto(settings.secret_key))
        if await store.load_session() is None:
            raise ValueError("Saved Session not found; SMS login is disabled for max-check")
        adapter = await MaxClient.create(
            account.phone,
            store,
            settings.data_dir,
            settings.max_app_version,
            saved_session_only=True,
        )
        if settings.max_pass:
            await adapter.password.queue.put(settings.max_pass.get_secret_value())
        await adapter.wait_ready()
        chats = await asyncio.wait_for(adapter.fetch_chats(), 30)
        print(f"Account {account_id}: MAX login OK, no SMS; chats={len(chats)}")
        for chat in chats[:10]:
            # Не печатаем имена, телефоны и содержимое переписки.
            print(f"chat_id={chat.id} type={chat.type}")
    finally:
        try:
            if adapter:
                await adapter.stop()
        finally:
            await engine.dispose()


def main():
    parser = argparse.ArgumentParser(prog="maxgate")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("gen-key")
    commands.add_parser("migrate")
    seed = commands.add_parser("seed-account")
    seed.add_argument("--owner", type=int, required=True)
    seed.add_argument("--session", type=Path, default=Path("temp/pymax/session.db"))
    check = commands.add_parser("max-check")
    check.add_argument("account_id", type=int)
    args = parser.parse_args()
    if args.command == "gen-key":
        print(Crypto.generate_key())
        return
    # Сторонние исключения/логи могут содержать credentials и payload.
    logging.disable(logging.CRITICAL)
    try:
        settings = Settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if args.command == "migrate":
            command.upgrade(migration_config(settings), "head")
            print("Database migrated")
        elif args.command == "seed-account":
            account_id = asyncio.run(seed_account(settings, args.owner, args.session))
            print(f"Account {account_id}: dev Account and saved Session ready")
        else:
            asyncio.run(max_check(settings, args.account_id))
    except Exception as exc:
        # Не включаем str(exc): сообщения библиотек могут содержать секреты.
        parser.exit(
            1, f"{args.command} failed ({type(exc).__name__}); check configuration and Session\n"
        )
