from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("inbox_mode IN ('private', 'supergroup')"),
        CheckConstraint(
            "state IN ('new','logging_in','password_required','active',"
            "'paused','session_lost','error')"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    phone: Mapped[str] = mapped_column(String)
    tg_bot_token_enc: Mapped[str] = mapped_column(Text)
    tg_bot_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    owner_tg_user_id: Mapped[int] = mapped_column(BigInteger)
    extra_owner_tg_user_ids: Mapped[list[int]] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    inbox_mode: Mapped[str] = mapped_column(default="private")
    inbox_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    relay_channels: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(default="new")
    state_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    @property
    def owner_ids(self) -> list[int]:
        """Все Telegram id Owner: основной первым, без повторов."""
        return list(dict.fromkeys([self.owner_tg_user_id, *(self.extra_owner_tg_user_ids or [])]))


class MaxSession(Base):
    __tablename__ = "max_sessions"
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    token_enc: Mapped[str] = mapped_column(Text)
    device_id: Mapped[str] = mapped_column(String)
    phone: Mapped[str] = mapped_column(String)
    mt_instance_id: Mapped[str] = mapped_column(String, default="")
    user_agent_json: Mapped[dict | None] = mapped_column(JSON)
    sync_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ChatLink(Base):
    __tablename__ = "chat_links"
    __table_args__ = (
        UniqueConstraint("account_id", "max_chat_id"),
        Index("ix_chat_links_account_topic", "account_id", "topic_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    max_chat_id: Mapped[int] = mapped_column(BigInteger)
    max_chat_type: Mapped[str] = mapped_column(String)
    max_title: Mapped[str | None] = mapped_column(String)
    topic_id: Mapped[int | None] = mapped_column(BigInteger)
    topic_title: Mapped[str | None] = mapped_column(String)
    renamed_by_owner: Mapped[bool] = mapped_column(default=False)
    muted: Mapped[bool] = mapped_column(default=False)
    last_relayed_time: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageLink(Base):
    __tablename__ = "message_links"
    __table_args__ = (
        Index("ix_message_links_account_tg", "account_id", "tg_message_id"),
        Index("ix_message_links_chat_max", "chat_link_id", "max_message_id"),
        CheckConstraint("direction IN ('max_to_tg','tg_to_max')"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    chat_link_id: Mapped[int] = mapped_column(ForeignKey("chat_links.id", ondelete="CASCADE"))
    max_message_id: Mapped[int] = mapped_column(BigInteger)
    tg_message_id: Mapped[int] = mapped_column(BigInteger)
    part: Mapped[int] = mapped_column(default=0)
    direction: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MaxUser(Base):
    __tablename__ = "max_users"
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    display_name: Mapped[str] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AccountEvent(Base):
    __tablename__ = "account_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
