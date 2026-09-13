import hmac
from typing import Literal

from aiogram import Bot
from aiohttp import web
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlalchemy import select

from maxgate.db.models import Account, AccountEvent
from maxgate.relay.storage import RelayStorage


class CreateAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=128)
    phone: str = Field(pattern=r"^\+[0-9]{7,15}$")
    tg_bot_token: SecretStr
    owner_tg_user_id: int = Field(gt=0)
    inbox_mode: Literal["private", "supergroup"] = "private"
    relay_channels: bool = False


class PatchAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(default=None, min_length=1, max_length=128)
    owner_tg_user_id: int = Field(default=None, gt=0)
    inbox_mode: Literal["private", "supergroup"] = None
    relay_channels: bool = None


def account_json(account):
    return {
        key: getattr(account, key)
        for key in (
            "id",
            "name",
            "phone",
            "owner_tg_user_id",
            "inbox_mode",
            "inbox_chat_id",
            "relay_channels",
            "state",
            "state_reason",
        )
    }


def chat_json(link):
    return {
        key: getattr(link, key)
        for key in (
            "id",
            "account_id",
            "max_chat_id",
            "max_chat_type",
            "max_title",
            "topic_id",
            "renamed_by_owner",
            "muted",
            "last_relayed_time",
        )
    }


class InternalApi:
    def __init__(self, supervisor, token, *, bot_factory=Bot):
        self.supervisor, self.token, self.bot_factory = supervisor, token, bot_factory
        self.app = web.Application(
            middlewares=[self.authenticate, self.errors], client_max_size=16384
        )
        self.app.add_routes(
            [
                web.get("/health", self.health),
                web.get("/accounts", self.accounts),
                web.post("/accounts", self.create),
                web.get("/accounts/{id}", self.get),
                web.patch("/accounts/{id}", self.patch),
                web.delete("/accounts/{id}", self.delete),
                web.post("/accounts/{id}/login/code", self.credential),
                web.post("/accounts/{id}/login/password", self.credential),
                web.get("/accounts/{id}/events", self.events),
                web.get("/accounts/{id}/chats", self.chats),
                web.post("/accounts/{id}/chats/{link_id}/{action:mute|unmute}", self.mute),
                web.post("/accounts/{id}/{action:login|pause|resume|logout}", self.action),
            ]
        )

    @web.middleware
    async def authenticate(self, request, handler):
        expected = "Bearer " + self.token
        actual = request.headers.get("Authorization", "")
        if not hmac.compare_digest(actual.encode(), expected.encode()):
            raise web.HTTPUnauthorized(text="Bearer token required")
        return await handler(request)

    @web.middleware
    async def errors(self, request, handler):
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except (ValidationError, ValueError, TypeError, KeyError):
            return web.json_response({"error": "Invalid request or Account state"}, status=400)
        except Exception:
            return web.json_response({"error": "Operation failed"}, status=502)

    async def _account(self, request):
        account = await self.supervisor.storage.get(int(request.match_info["id"]))
        if account is None:
            raise web.HTTPNotFound(text="Account not found")
        return account

    async def health(self, request):
        return web.json_response({"status": "ok"})

    async def accounts(self, request):
        return web.json_response([account_json(a) for a in await self.supervisor.storage.all()])

    async def get(self, request):
        return web.json_response(account_json(await self._account(request)))

    async def create(self, request):
        data = CreateAccount.model_validate(await request.json())
        bot = self.bot_factory(data.tg_bot_token.get_secret_value())
        try:
            me = await bot.get_me()
            if data.inbox_mode == "private" and not me.has_topics_enabled:
                raise web.HTTPBadRequest(text="Bot requires has_topics_enabled")
        finally:
            await bot.session.close()
        account = Account(
            name=data.name,
            phone=data.phone,
            tg_bot_token_enc=self.supervisor.crypto.encrypt(data.tg_bot_token.get_secret_value()),
            owner_tg_user_id=data.owner_tg_user_id,
            inbox_mode=data.inbox_mode,
            relay_channels=data.relay_channels,
        )
        async with self.supervisor.sessions.begin() as session:
            session.add(account)
            await session.flush()
        await self.supervisor.storage.event(account.id, "Account created")
        return web.json_response(account_json(account), status=201)

    async def patch(self, request):
        account = await self._account(request)
        data = PatchAccount.model_validate(await request.json()).model_dump(exclude_unset=True)
        return web.json_response(account_json(await self.supervisor.patch(account.id, data)))

    async def delete(self, request):
        account = await self._account(request)
        await self.supervisor.action(account.id, "delete")
        return web.Response(status=204)

    async def action(self, request):
        account = await self._account(request)
        await self.supervisor.action(account.id, request.match_info["action"])
        return web.json_response({"accepted": True}, status=202)

    async def credential(self, request):
        account = await self._account(request)
        kind = request.path.rsplit("/", 1)[-1]
        body = await request.json()
        value = body.get(kind)
        if not isinstance(value, str) or not 1 <= len(value) <= 1024:
            raise web.HTTPBadRequest(text="Credential required")
        from maxgate.diagnostics import register_secret

        register_secret(value)
        await self.supervisor.provide(account.id, kind, value)
        return web.json_response({"accepted": True}, status=202)

    async def events(self, request):
        account = await self._account(request)
        async with self.supervisor.sessions() as session:
            rows = await session.scalars(
                select(AccountEvent)
                .where(AccountEvent.account_id == account.id)
                .order_by(AccountEvent.id.desc())
                .limit(1000)
            )
            return web.json_response(
                [
                    dict(id=row.id, ts=row.ts.isoformat(), level=row.level, message=row.message)
                    for row in rows
                ]
            )

    async def chats(self, request):
        account = await self._account(request)
        storage = RelayStorage(account.id, self.supervisor.sessions)
        return web.json_response([chat_json(link) for link in await storage.chats()])

    async def mute(self, request):
        account = await self._account(request)
        storage = RelayStorage(account.id, self.supervisor.sessions)
        link = await storage.chat(link_id=int(request.match_info["link_id"]))
        if link is None:
            raise web.HTTPNotFound(text="ChatLink not found")
        updated = await storage.change_chat(link.id, muted=request.match_info["action"] == "mute")
        return web.json_response(chat_json(updated))
