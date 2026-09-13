from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from sqlalchemy import update

from maxgate.db.models import Account
from maxgate.tg import media
from maxgate.tg.messages import from_telegram

TOPIC_COLORS = {"dialog": 0x8EEE98, "group": 0x6FB9F0, "chat": 0x6FB9F0, "channel": 0xFB6F5F}


class TgBot:
    """Bot и Dispatcher одного Account; обработчики Relay подключаются runner'ом."""

    def __init__(self, token, account, session_factory, *, bot=None):
        self.bot = bot or Bot(token)
        self.account = account
        self._sessions = session_factory
        self.dispatcher = Dispatcher()
        self.on_message = None
        self.on_edit = None
        self.on_topic_edited = None
        self._pending_titles: dict[int, str] = {}
        self.dispatcher.message.outer_middleware(self._owner_only)
        self.dispatcher.edited_message.outer_middleware(self._owner_only)
        self.dispatcher.message.register(self._start, CommandStart())
        self.dispatcher.message.register(self._message)
        self.dispatcher.edited_message.register(self._edited_message)

    async def _owner_only(self, handler, event: Message, data):
        if not event.from_user or event.from_user.id != self.account.owner_tg_user_id:
            if event.text and event.text.split()[0].split("@")[0] == "/start":
                await self.bot.send_message(chat_id=event.chat.id, text="Доступ только для Owner")
            return None
        return await handler(event, data)

    async def _start(self, message: Message):
        if self.account.inbox_mode == "private":
            if message.chat.type != "private":
                return
            me = await self.bot.get_me()
            if not me.has_topics_enabled:
                await self.bot.send_message(
                    chat_id=message.chat.id, text="Включите Topics у бота в BotFather"
                )
                return
        else:
            if message.chat.type != "supergroup" or not message.chat.is_forum:
                await self.bot.send_message(chat_id=message.chat.id, text="Inbox требует форум")
                return
            me = await self.bot.get_me()
            membership = await self.bot.get_chat_member(message.chat.id, me.id)
            if not getattr(membership, "can_manage_topics", False):
                await self.bot.send_message(
                    chat_id=message.chat.id, text="Боту нужно право can_manage_topics"
                )
                return
        async with self._sessions.begin() as session:
            await session.execute(
                update(Account)
                .where(Account.id == self.account.id)
                .values(inbox_chat_id=message.chat.id)
            )
        self.account.inbox_chat_id = message.chat.id
        await self.bot.send_message(chat_id=message.chat.id, text="Inbox подключён")

    def _in_inbox(self, message):
        return message.chat.id == self.account.inbox_chat_id

    async def _message(self, message: Message):
        if not self._in_inbox(message):
            return
        if message.forum_topic_edited:
            title = message.forum_topic_edited.name
            topic_id = message.message_thread_id
            if self._pending_titles.get(topic_id) == title:
                self._pending_titles.pop(topic_id, None)
            elif self.on_topic_edited:
                await self.on_topic_edited(topic_id, title)
            return
        if not message.message_thread_id:
            await self.bot.send_message(
                chat_id=message.chat.id, text="Пишите в Topic чата MAX. Команды: /status, /start"
            )
            return
        relay = from_telegram(message)
        if relay.notes:
            await self.bot.send_message(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="\n".join(note.text for note in relay.notes),
            )
            return
        if any(a.size and a.size > media.DOWNLOAD_LIMIT for a in relay.attachments):
            await self.bot.send_message(
                chat_id=message.chat.id,
                message_thread_id=message.message_thread_id,
                text="⛔ Telegram не отдаёт ботам файлы больше 20 МБ",
            )
            return
        if self.on_message:
            await self.on_message(message, relay)

    async def _edited_message(self, message: Message):
        if self._in_inbox(message) and message.message_thread_id and self.on_edit:
            await self.on_edit(message, from_telegram(message))

    async def create_topic(self, title: str, chat_type: str) -> int:
        topic = await self.bot.create_forum_topic(
            chat_id=self.account.inbox_chat_id,
            name=(title.strip() or "MAX")[:128],
            icon_color=TOPIC_COLORS[chat_type.lower()],
        )
        return topic.message_thread_id

    async def edit_topic(self, topic_id: int, title: str):
        title = (title.strip() or "MAX")[:128]
        self._pending_titles[topic_id] = title
        try:
            await self.bot.edit_forum_topic(
                chat_id=self.account.inbox_chat_id, message_thread_id=topic_id, name=title
            )
        except BaseException:
            self._pending_titles.pop(topic_id, None)
            raise

    async def send(self, topic_id, message):
        return await media.send(self.bot, self.account.inbox_chat_id, topic_id, message)

    async def download(self, attachment, dest):
        return await media.download(self.bot, attachment, dest)

    async def start(self):
        await self.dispatcher.start_polling(
            self.bot, handle_signals=False, allowed_updates=["message", "edited_message"]
        )

    async def stop(self):
        try:
            await self.dispatcher.stop_polling()
        except RuntimeError:
            pass  # polling ещё не запускался
        finally:
            await self.bot.session.close()
