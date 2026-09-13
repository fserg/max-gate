"""Шаг 0: живая проверка PyMax (см. docs/design.md, раздел 13).

Одноразовый скрипт без базы и без Telegram. Управляется файлами в temp/ (папка в .gitignore):

  temp/sms_code.txt   код из SMS: скрипт ждёт появления файла, читает и удаляет его
  temp/password.txt   пароль 2FA, если MAX его попросит (тот же механизм)
  temp/cmd.txt        команды, по одной на строку; исполняются, файл очищается:
      chats                          список чатов
      send <chat_id> <текст>         отправить текст
      photo <chat_id> <путь>         отправить фото
      file <chat_id> <путь>          отправить файл
      history <chat_id> [n]          последние n сообщений (по умолчанию 10)
      edit <chat_id> <msg_id> <текст>
      delete <chat_id> <msg_id>
      read <chat_id> <msg_id>
      user <user_id>
      stop                           штатно завершить

Лог: temp/spike.log и stdout. Сессия: temp/pymax/session.db. Скачанные вложения: temp/downloads/.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import shlex
import ssl
import sys
import time
from importlib import resources
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp
from pymax import Client, File, Message, Photo
from pymax.config import ExtraConfig
from pymax.versions.catalog import VersionCatalog

try:
    from pymax.types.domain import (
        AudioAttachment,
        FileAttachment,
        PhotoAttachment,
        StickerAttachment,
        UnknownAttachment,
        VideoAttachment,
    )
except ImportError:  # на случай другой раскладки модулей
    from pymax.types.domain.attachments import (  # type: ignore[no-redef]
        AudioAttachment,
        FileAttachment,
        PhotoAttachment,
        StickerAttachment,
        UnknownAttachment,
        VideoAttachment,
    )

ROOT = Path(__file__).resolve().parent.parent
TEMP = ROOT / "temp"
SMS_FILE = TEMP / "sms_code.txt"
PASSWORD_FILE = TEMP / "password.txt"
CMD_FILE = TEMP / "cmd.txt"
DOWNLOADS = TEMP / "downloads"
LOG_FILE = TEMP / "spike.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("spike")


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'\"")
    return env


def ts(ms: int | None) -> str:
    if not ms:
        return "-"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%d.%m %H:%M:%S")


def oneme_ssl_context() -> ssl.SSLContext:
    """SSL-контекст только для хостов *.oneme.ru: системное доверие плюс корень
    «Russian Trusted Root CA», который PyMax возит внутри пакета для своего API.
    Системное хранилище не трогается."""
    ctx = ssl.create_default_context()
    ca = resources.files("pymax._data") / "rootca_ssl_rsa2022.crt"
    ctx.load_verify_locations(cadata=ca.read_text(encoding="utf-8"))
    return ctx


def is_oneme(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "oneme.ru" or host.endswith(".oneme.ru")


class FileProvider:
    """Провайдер SMS-кода и пароля: ждёт появления файла и читает его."""

    def __init__(self, path: Path, label: str, timeout: float = 180.0) -> None:
        self.path = path
        self.label = label
        self.timeout = timeout

    async def _wait(self) -> str:
        log.info("ЖДУ %s: положите его в %s (таймаут %.0f с)", self.label, self.path, self.timeout)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.path.exists():
                value = self.path.read_text(encoding="utf-8").strip()
                self.path.unlink(missing_ok=True)
                if value:
                    log.info("%s получен (%d симв.)", self.label, len(value))
                    return value
            await asyncio.sleep(0.5)
        raise TimeoutError(f"{self.label} не получен за {self.timeout:.0f} с")

    async def get_code(self, phone: str) -> str:  # SmsCodeProvider
        return await self._wait()

    async def get_password(self, hint: str | None = None) -> str:  # PasswordProvider
        log.info("MAX просит пароль 2FA, подсказка: %r", hint)
        env_password = os.environ.get("MAX_PASS")
        if env_password:
            log.info("Беру пароль из MAX_PASS")
            return env_password
        return await self._wait()


class Spike:
    def __init__(self, client: Client) -> None:
        self.client = client
        self.me_id: int | None = None
        self.poller: asyncio.Task[None] | None = None
        self.user_names: dict[int, str] = {}
        self.stopping = False
        self.ssl_oneme = oneme_ssl_context()

    # ---------- вспомогательное ----------

    async def user_name(self, user_id: int | None) -> str:
        if user_id is None:
            return "?"
        if user_id in self.user_names:
            return self.user_names[user_id]
        name = f"id{user_id}"
        try:
            user = await self.client.get_user(user_id)
            if user is not None and user.names:
                n = user.names[0]
                name = n.name or " ".join(x for x in (n.first_name, n.last_name) if x) or name
        except Exception as exc:  # noqa: BLE001
            log.warning("get_user(%s) не удался: %r", user_id, exc)
        self.user_names[user_id] = name
        return name

    async def download(self, url: str, dest: Path) -> None:
        async with aiohttp.ClientSession() as http:
            for _ in range(6):
                if urlparse(url).scheme != "https":
                    raise ValueError("Media URL must use HTTPS")
                ssl_arg = self.ssl_oneme if is_oneme(url) else True
                async with http.get(url, ssl=ssl_arg, allow_redirects=False) as resp:
                    if resp.status in {301, 302, 303, 307, 308}:
                        url = urljoin(url, resp.headers["Location"])
                        continue
                    resp.raise_for_status()
                    data = await resp.read()
                    dest.write_bytes(data)
                    head = data[:12]
                    kind = (
                        "OGG" if head.startswith(b"OggS")
                        else "MP4/M4A" if head[4:8] == b"ftyp"
                        else "MP3" if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3")
                        else "JPEG" if head.startswith(b"\xff\xd8")
                        else "PNG" if head.startswith(b"\x89PNG")
                        else "WEBP" if head[8:12] == b"WEBP"
                        else "?"
                    )
                    log.info(
                        "  скачано %s: %d байт, content-type=%s, сигнатура=%s",
                        dest.name, len(data), resp.headers.get("Content-Type"), kind,
                    )
                    return
            raise ValueError("Too many media redirects")

    async def describe_attachments(self, message: Message) -> None:
        for n, att in enumerate(message.attaches or []):
            kind = type(att).__name__
            chat_id = message.chat_id
            try:
                if isinstance(att, PhotoAttachment):
                    log.info("  [%d] PHOTO %sx%s photo_id=%s url=%s", n, att.width, att.height, att.photo_id, att.base_url)
                    if att.base_url:
                        await self.download(att.base_url, DOWNLOADS / f"{message.id}_{n}.jpg")
                elif isinstance(att, FileAttachment):
                    log.info("  [%d] FILE name=%r size=%s file_id=%s", n, att.name, att.size, att.file_id)
                    info = await self.client.get_file_by_id(chat_id=chat_id, message_id=message.id, file_id=att.file_id)
                    log.info("  get_file_by_id -> url=%s", getattr(info, "url", None))
                    if info and info.url:
                        await self.download(info.url, DOWNLOADS / f"{message.id}_{n}_{att.name or 'file'}")
                elif isinstance(att, VideoAttachment):
                    log.info("  [%d] VIDEO %sx%s dur=%s video_type=%s video_id=%s", n, att.width, att.height, att.duration, att.video_type, att.video_id)
                    info = await self.client.get_video_by_id(chat_id=chat_id, message_id=message.id, video_id=att.video_id)
                    log.info("  get_video_by_id -> url=%s", getattr(info, "url", None))
                    if info and info.url:
                        await self.download(info.url, DOWNLOADS / f"{message.id}_{n}.mp4")
                elif isinstance(att, AudioAttachment):
                    log.info("  [%d] AUDIO dur=%s audio_id=%s url=%s transcription=%s", n, att.duration, att.audio_id, att.url, att.transcription_status)
                    if att.url:
                        await self.download(att.url, DOWNLOADS / f"{message.id}_{n}.audio")
                elif isinstance(att, StickerAttachment):
                    log.info("  [%d] STICKER id=%s url=%s lottie=%s", n, att.sticker_id, att.url, att.lottie_url)
                elif isinstance(att, UnknownAttachment):
                    log.info("  [%d] UNKNOWN type=%s extra=%s", n, att.type, att.model_extra)
                else:
                    log.info("  [%d] %s %s", n, kind, att.model_dump(exclude_none=True) if hasattr(att, "model_dump") else att)
            except Exception as exc:  # noqa: BLE001
                log.exception("  [%d] %s: ошибка обработки: %r", n, kind, exc)

    async def describe_message(self, tag: str, message: Message) -> None:
        mine = self.me_id is not None and message.sender == self.me_id
        sender = await self.user_name(message.sender)
        link = message.link
        link_s = ""
        if link is not None:
            link_s = f" link={type(link).__name__}(msg_id={getattr(getattr(link, 'message', None), 'id', None)})"
        elements = [(e.type, e.from_, e.length) for e in (message.elements or [])]
        log.info(
            "[%s] chat=%s id=%s time=%s sender=%s%s type=%s status=%s text=%r attaches=%d%s%s",
            tag, message.chat_id, message.id, ts(message.time), sender,
            " (Я)" if mine else "", message.type, message.status, (message.text or "")[:200],
            len(message.attaches or []), link_s, f" elements={elements}" if elements else "",
        )
        if message.attaches:
            await self.describe_attachments(message)

    async def chats_summary(self) -> None:
        chats = await self.client.fetch_chats()
        log.info("fetch_chats: %d чатов", len(chats))
        chats = sorted(chats, key=lambda c: c.last_event_time or 0, reverse=True)
        for c in chats[:30]:
            title = c.title
            if c.is_dialog and not title:
                others = [uid for uid in (c.participants or {}) if uid != self.me_id]
                title = await self.user_name(others[0]) if others else "?"
            last = c.last_message
            last_s = f" last={ts(last.time)} {(last.text or '')[:40]!r}" if last else ""
            log.info(
                "  chat id=%s type=%s title=%r participants=%s unread=%s%s",
                c.id, c.type, title, c.participants_count, c.new_messages, last_s,
            )

    # ---------- команды ----------

    async def run_command(self, line: str) -> None:
        parts = shlex.split(line)
        if not parts:
            return
        cmd, args = parts[0].lower(), parts[1:]
        c = self.client
        log.info(">>> %s", line)
        try:
            if cmd == "chats":
                await self.chats_summary()
            elif cmd == "send":
                msg = await c.send_message(int(args[0]), text=" ".join(args[1:]))
                log.info("send_message -> id=%s time=%s", msg.id, ts(msg.time))
            elif cmd == "photo":
                msg = await c.send_message(int(args[0]), text=" ".join(args[2:]) or None, attachments=[Photo(path=args[1])])
                log.info("send photo -> id=%s", msg.id)
            elif cmd == "file":
                msg = await c.send_message(int(args[0]), text=" ".join(args[2:]) or None, attachments=[File(path=args[1])])
                log.info("send file -> id=%s", msg.id)
            elif cmd == "history":
                n = int(args[1]) if len(args) > 1 else 10
                msgs = await c.fetch_history(int(args[0]), backward=n)
                log.info("fetch_history(backward=%d) -> %d сообщений", n, len(msgs))
                for m in msgs:
                    await self.describe_message("HIST", m)
            elif cmd == "edit":
                msg = await c.edit_message(int(args[0]), int(args[1]), text=" ".join(args[2:]))
                log.info("edit_message -> id=%s status=%s text=%r", msg.id, msg.status, msg.text)
            elif cmd == "delete":
                ok = await c.delete_message(int(args[0]), [int(args[1])], for_me=False)
                log.info("delete_message -> %s", ok)
            elif cmd == "read":
                state = await c.read_message(int(args[1]), int(args[0]))
                log.info("read_message -> %s", state)
            elif cmd == "getfile":
                info = await c.get_file_by_id(chat_id=int(args[0]), message_id=int(args[1]), file_id=int(args[2]))
                log.info("get_file_by_id -> url=%s", getattr(info, "url", None))
                if info and info.url:
                    await self.download(info.url, DOWNLOADS / f"{args[1]}_{args[2]}.bin")
            elif cmd == "user":
                user = await c.get_user(int(args[0]))
                log.info("get_user -> %s", user.model_dump(exclude_none=True) if user else None)
            elif cmd == "stop":
                self.stopping = True
                log.info("Останавливаюсь по команде")
                await c.stop()
            else:
                log.warning("Неизвестная команда: %s", cmd)
        except Exception as exc:  # noqa: BLE001
            log.exception("Команда %r завершилась ошибкой: %r", line, exc)

    async def poll_commands(self) -> None:
        CMD_FILE.touch(exist_ok=True)
        while not self.stopping:
            try:
                text = CMD_FILE.read_text(encoding="utf-8")
                if text.strip():
                    CMD_FILE.write_text("", encoding="utf-8")
                    for line in text.splitlines():
                        if line.strip():
                            await self.run_command(line)
            except Exception as exc:  # noqa: BLE001
                log.exception("poll_commands: %r", exc)
            await asyncio.sleep(1.0)

    # ---------- обработчики ----------

    def register(self) -> None:
        c = self.client

        @c.on_start()
        async def on_start(client: Client) -> None:
            me = client.me
            self.me_id = me.contact.id if me else None
            names = me.contact.names[0].name if me and me.contact.names else "?"
            log.info("=== START: вошёл как id=%s name=%r ===", self.me_id, names)
            if self.poller is None:
                self.poller = asyncio.create_task(self.poll_commands())
                await self.chats_summary()
                log.info("Готов: слушаю события. Команды в %s", CMD_FILE)

        @c.on_message()
        async def on_message(message: Message, client: Client) -> None:
            await self.describe_message("MSG", message)

        @c.on_message_edit()
        async def on_edit(message: Message, client: Client) -> None:
            await self.describe_message("EDIT", message)

        @c.on_message_delete()
        async def on_delete(event, client: Client) -> None:
            log.info("[DELETE] chat=%s ids=%s ttl=%s", event.chat_id, event.message_ids, event.ttl)

        @c.on_message_read()
        async def on_read(event, client: Client) -> None:
            log.info("[READ] chat=%s user=%s mark=%s", event.chat_id, event.user_id, ts(event.mark))

        @c.on_chat_update()
        async def on_chat(chat, client: Client) -> None:
            log.info("[CHAT] id=%s type=%s title=%r participants=%s", chat.id, chat.type, chat.title, chat.participants_count)

        @c.on_raw()
        async def on_raw(frame, client: Client) -> None:
            if frame.opcode in (1,):  # ping
                return
            payload = frame.payload if isinstance(frame.payload, dict) else {}
            log.debug("[RAW] opcode=%s cmd=%s keys=%s", frame.opcode, frame.cmd, list(payload)[:8])

        @c.on_disconnect()
        async def on_disconnect(exc, reconnect, delay) -> None:
            log.warning("[DISCONNECT] %r reconnect=%s delay=%s", exc, reconnect, delay)

        @c.on_error()
        async def on_error(exc, ctx) -> None:
            log.error("[ERROR] %r ctx=%s", exc, ctx)


async def main() -> None:
    env = load_env(ROOT / ".env")
    phone = env.get("TEL") or os.environ.get("TEL")
    if not phone:
        raise SystemExit("TEL не найден в .env")
    if env.get("MAX_PASS") and not os.environ.get("MAX_PASS"):
        os.environ["MAX_PASS"] = env["MAX_PASS"]
    log.info("Пароль 2FA в .env: %s", "есть" if os.environ.get("MAX_PASS") else "нет")
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    (TEMP / "pymax").mkdir(parents=True, exist_ok=True)

    catalog = VersionCatalog(remote=True)
    try:
        await catalog.load()
    except Exception as exc:  # noqa: BLE001
        log.warning("Удалённый каталог версий не загрузился: %r; использую встроенный", exc)
    versions = sorted(catalog.versions, key=lambda v: tuple(int(x) for x in v.split(".")))
    app_version = os.environ.get("MAX_APP_VERSION") or versions[-1]
    log.info("Версий в каталоге: %d, последняя %s, использую %s (рекомендуемая PyMax %s)",
             len(versions), versions[-1], app_version, VersionCatalog.recommended())

    client = Client(
        phone=phone,
        work_dir=str(TEMP / "pymax"),
        session_name="session.db",
        sms_code_provider=FileProvider(SMS_FILE, "SMS-код"),
        password_provider=FileProvider(PASSWORD_FILE, "пароль 2FA"),
        app_version=app_version,
        catalog=catalog,
        extra_config=ExtraConfig(log_level="INFO"),
    )
    spike = Spike(client)
    spike.register()
    log.info("Запускаю клиент для номера ...%s", phone[-4:])
    try:
        await asyncio.wait_for(client.start(), timeout=3 * 3600)
    except asyncio.TimeoutError:
        log.warning("3 часа истекли, завершаю")
        await client.stop()
    log.info("=== КОНЕЦ ===")


if __name__ == "__main__":
    asyncio.run(main())
