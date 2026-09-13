import asyncio
from contextlib import suppress
from pathlib import Path

from aiohttp import ClientError
from pymax import Client, File, Photo, Video, Voice
from pymax.config import ExtraConfig
from pymax.exceptions import ApiError
from pymax.versions.catalog import VersionCatalog

from maxgate.diagnostics import route_pymax_logging
from maxgate.domain import Attachment, RelayMessage, max_elements
from maxgate.max.media import AttachmentUnavailable, download
from maxgate.max.plaintext import GateMessageService
from maxgate.max.providers import PasswordProvider, SavedSessionOnly, SmsCodeProvider
from maxgate.max.uploads import GateUploadService
from maxgate.relay.errors import permanent_max_error


class SessionLost(RuntimeError):
    pass


def is_session_lost(exc):
    return isinstance(exc, SessionLost) or (
        isinstance(exc, ApiError)
        and any(
            code in (exc.error, exc.message) for code in ("FAIL_LOGIN_TOKEN", "FAIL_LOGOUT_ALL")
        )
    )


class GateClient(Client):
    """PyMax 2.4.1: прерывает цикл start при отзыве Session и relogin=False."""

    async def send_message(self, *args, elements=None, **kwargs):
        return await self._app.api.messages.send_message(*args, elements=elements, **kwargs)

    async def edit_message(self, *args, elements=None, **kwargs):
        return await self._app.api.messages.edit_message(*args, elements=elements, **kwargs)

    def _build_app(self):
        app = super()._build_app()
        app.api.uploads = GateUploadService(app.api.uploads)
        app.api.messages = GateMessageService(app)
        start = app.start

        async def guarded_start():
            try:
                return await start()
            except ApiError as exc:
                if is_session_lost(exc):
                    raise SessionLost("Session MAX revoked") from None
                raise

        app.start = guarded_start
        return app


class MaxClient:
    def __init__(self, client, sms=None, password=None):
        self.client = client
        self.sms = sms or SmsCodeProvider()
        self.password = password or PasswordProvider()
        self.ready = asyncio.Event()
        self.task: asyncio.Task | None = None

        @client.on_start()
        async def on_start(_):
            self.ready.set()

    @classmethod
    async def create(
        cls, phone, store, data_dir: Path, app_version=None, *, saved_session_only=False
    ):
        catalog = VersionCatalog(remote=True)
        try:
            await asyncio.wait_for(catalog.load(), 20)
        except (ClientError, OSError, TimeoutError, ValueError):
            catalog = VersionCatalog()
            await catalog.load()
        catalog.remote = False  # каталог уже загружен для этого запуска
        version = app_version or max(catalog.versions, key=lambda v: tuple(map(int, v.split("."))))
        sms, password = SmsCodeProvider(), PasswordProvider()
        route_pymax_logging()
        client = GateClient(
            phone=phone,
            work_dir=str(data_dir),
            app_version=version,
            catalog=catalog,
            sms_code_provider=sms,
            password_provider=password,
            auth_flow=SavedSessionOnly() if saved_session_only else None,
            extra_config=ExtraConfig(store=store, relogin=False, log_level="WARNING"),
        )
        return cls(client, sms, password)

    def on(self, event: str, callback):
        if event not in {
            "start",
            "message",
            "message_edit",
            "message_delete",
            "chat_update",
            "disconnect",
            "error",
        }:
            raise ValueError("Unsupported MAX event")
        getattr(self.client, f"on_{event}")()(callback)

    def start(self) -> asyncio.Task:
        if self.task is None or self.task.done():
            self.ready.clear()
            self.task = asyncio.create_task(self.client.start())
        return self.task

    async def wait_ready(self, timeout=60):
        task = self.start()
        waiter = asyncio.create_task(self.ready.wait())
        try:
            done, _ = await asyncio.wait(
                {task, waiter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                await task
                raise RuntimeError("MAX stopped before login")
            if waiter not in done:
                raise TimeoutError("MAX login timed out")
        finally:
            waiter.cancel()
            with suppress(asyncio.CancelledError):
                await waiter

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        self.ready.clear()

    @property
    def me_id(self):
        me = self.client.me
        return me.contact.id if me else None

    async def get_chat(self, chat_id):
        return await self.client.get_chat(chat_id)

    async def user_name(self, user_id):
        if user_id is None:
            return "?"
        user = await self.client.get_user(user_id)
        if user:
            for name in user.names or []:
                display = name.name or " ".join(n for n in (name.first_name, name.last_name) if n)
                if display:
                    return display
            if user.phone:
                return user.phone
        return f"MAX {user_id}"

    async def fetch_chats(self):
        return await self.client.fetch_chats()

    async def fetch_history(self, chat_id: int, **kwargs):
        messages = await self.client.fetch_history(chat_id, **kwargs)
        return [
            m.model_copy(update={"chat_id": chat_id}) if m.chat_id is None else m for m in messages
        ]

    async def download_attachment(self, chat_id, message_id, attachment, dest: Path):
        if isinstance(attachment, str):
            if not attachment:
                raise AttachmentUnavailable("Attachment has no downloadable URL")
            return await download(attachment, dest)
        kind = attachment.type
        if kind == "FILE":
            info = await self.client.get_file_by_id(
                chat_id=chat_id, message_id=message_id, file_id=attachment.file_id
            )
            url = info.url if info else None
        elif kind == "VIDEO":
            info = await self.client.get_video_by_id(
                chat_id=chat_id, message_id=message_id, video_id=attachment.video_id
            )
            url = info.url if info else None
        else:
            url = getattr(attachment, "base_url", None) or getattr(attachment, "url", None)
            if kind == "STICKER":
                url = getattr(attachment, "lottie_url", None) or url
        if not url:
            raise AttachmentUnavailable("Attachment has no downloadable URL")
        return await download(url, dest)

    async def send(self, chat_id: int, message: RelayMessage):
        constructors = {
            "photo": Photo,
            "video": Video,
            "document": File,
            "audio": File,
            "voice": Voice,
        }
        attachments = []
        for attachment in message.attachments:
            kwargs = dict(path=str(attachment.source), name=attachment.name)
            if attachment.kind == "voice" and attachment.duration is not None:
                kwargs["duration"] = attachment.duration
            attachments.append(constructors[attachment.kind](**kwargs))
        try:
            return await self.client.send_message(
                chat_id,
                text=message.text or None,
                elements=max_elements(message.text, message.entities),
                reply_to=message.reply_to,
                attachments=attachments or None,
            )
        except Exception as exc:
            if permanent_max_error(exc) or not any(a.kind == "voice" for a in message.attachments):
                raise
            # PyMax 2.4.1 иногда не завершает загрузку Voice (исследование, §6).
            fallback = RelayMessage(
                text=message.text + "\n🎤 Голосовое сообщением-файлом",
                entities=message.entities,
                reply_to=message.reply_to,
                attachments=[
                    Attachment(
                        "document" if a.kind == "voice" else a.kind,
                        a.source,
                        a.name,
                        a.size,
                        a.mime,
                        a.duration,
                    )
                    for a in message.attachments
                ],
            )
            return await self.send(chat_id, fallback)

    async def edit(self, chat_id: int, message_id: int, text: str, *, entities=None):
        return await self.client.edit_message(
            chat_id, message_id, text=text, elements=max_elements(text, entities)
        )

    async def add_reaction(self, chat_id, message_id, emoji):
        return await self.client.add_reaction(chat_id, message_id, emoji)

    async def delete(self, chat_id, message_id):
        return await self.client.delete_message(chat_id, [message_id], for_me=False)

    async def remove_reaction(self, chat_id, message_id):
        return await self.client.remove_reaction(chat_id, message_id)
