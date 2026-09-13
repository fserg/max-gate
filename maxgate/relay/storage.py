"""Операции хранения Relay, всегда ограниченные Account."""

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert

from maxgate.db.models import ChatLink, MessageLink, utcnow


class RelayStorage:
    def __init__(self, account_id, sessions):
        self.account_id, self.sessions = account_id, sessions

    async def chats(self):
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(ChatLink).where(ChatLink.account_id == self.account_id)
                )
            )

    async def chat(self, *, max_chat_id=None, topic_id=None, link_id=None):
        query = select(ChatLink).where(ChatLink.account_id == self.account_id)
        if max_chat_id is not None:
            query = query.where(ChatLink.max_chat_id == max_chat_id)
        elif topic_id is not None:
            query = query.where(ChatLink.topic_id == topic_id)
        elif link_id is not None:
            query = query.where(ChatLink.id == link_id)
        else:
            return None
        async with self.sessions() as session:
            return await session.scalar(query)

    async def ensure_chat(self, chat_id, kind, title, timestamp):
        async with self.sessions.begin() as session:
            await session.execute(
                insert(ChatLink)
                .values(
                    account_id=self.account_id,
                    max_chat_id=chat_id,
                    max_chat_type=kind,
                    max_title=title,
                    last_relayed_time=timestamp,
                )
                .on_conflict_do_nothing(index_elements=["account_id", "max_chat_id"])
            )
        return await self.chat(max_chat_id=chat_id)

    async def change_chat(self, link_id, **changes):
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(ChatLink).where(
                    ChatLink.account_id == self.account_id, ChatLink.id == link_id
                )
            )
            if row is None:
                raise ValueError("ChatLink not found")
            for key, value in changes.items():
                if key == "last_relayed_time":
                    value = max(row.last_relayed_time, value)
                setattr(row, key, value)
        return row

    async def messages(self, link_id, *, max_id=None, tg_id=None):
        query = select(MessageLink).where(
            MessageLink.account_id == self.account_id,
            MessageLink.chat_link_id == link_id,
            MessageLink.created_at >= utcnow() - timedelta(days=90),
        )
        if max_id is not None:
            query = query.where(MessageLink.max_message_id == max_id)
        if tg_id is not None:
            query = query.where(MessageLink.tg_message_id == tg_id)
        async with self.sessions() as session:
            return list(await session.scalars(query.order_by(MessageLink.part, MessageLink.id)))

    async def link_messages(self, link_id, max_id, tg_ids, direction):
        async with self.sessions.begin() as session:
            existing = set(
                await session.scalars(
                    select(MessageLink.tg_message_id).where(
                        MessageLink.account_id == self.account_id,
                        MessageLink.chat_link_id == link_id,
                        MessageLink.max_message_id == max_id,
                    )
                )
            )
            for part, tg_id in enumerate(tg_ids):
                if tg_id not in existing:
                    session.add(
                        MessageLink(
                            account_id=self.account_id,
                            chat_link_id=link_id,
                            max_message_id=max_id,
                            tg_message_id=tg_id,
                            part=part,
                            direction=direction,
                        )
                    )

    async def cleanup(self):
        async with self.sessions.begin() as session:
            await session.execute(
                delete(MessageLink).where(
                    MessageLink.account_id == self.account_id,
                    MessageLink.created_at < utcnow() - timedelta(days=90),
                )
            )

    async def forget_messages(self, link_id):
        async with self.sessions.begin() as session:
            await session.execute(
                delete(MessageLink).where(
                    MessageLink.account_id == self.account_id, MessageLink.chat_link_id == link_id
                )
            )
