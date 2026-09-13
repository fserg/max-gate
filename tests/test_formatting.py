from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import Message

from maxgate.domain import (
    Entity,
    RelayMessage,
    join_text,
    max_elements,
    split_text,
    telegram_entities,
    utf16_length,
)
from maxgate.tg.bot import TgBot
from maxgate.tg.messages import from_telegram


def test_utf16_nested_crossing_and_unsupported_entities():
    text = "😀abcdef"
    entities = [
        Entity("bold", 0, 6),
        Entity("italic", 2, 2),
        Entity("underline", 4, 4),
        Entity("code", 1, 1),
        Entity("mention", 2, 3),
    ]
    assert max_elements(text, entities) == [
        {"type": "STRONG", "from": 0, "length": 6},
        {"type": "EMPHASIZED", "from": 2, "length": 2},
        {"type": "UNDERLINE", "from": 4, "length": 4},
    ]
    assert text == "😀abcdef"


def test_all_supported_entities_and_url_split():
    types = {
        "bold": "STRONG",
        "italic": "EMPHASIZED",
        "underline": "UNDERLINE",
        "strikethrough": "STRIKETHROUGH",
        "code": "MONOSPACED",
        "pre": "CODE",
        "text_link": "LINK",
        "blockquote": "QUOTE",
    }
    for kind, target in types.items():
        result = max_elements(
            "test", [Entity(kind, 0, 4, "https://example.org" if kind == "text_link" else None)]
        )
        assert result[0]["type"] == target and result[0]["from"] == 0
    text = "😀 https://example.org"
    normalized = telegram_entities(text, [Entity("url", 3, utf16_length("https://example.org"))])
    parts = split_text(RelayMessage(text, normalized), limit=8)
    assert "".join(p.text for p in parts) == text
    for part in parts:
        for e in max_elements(part.text, part.entities):
            assert e["attributes"]["url"] == "https://example.org"
            assert e["from"] + e["length"] <= utf16_length(part.text)


def test_caption_forward_prefix_and_album_offset():
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat={"id": 1, "type": "private"},
        caption="😀abc",
        caption_entities=[{"type": "bold", "offset": 2, "length": 3}],
        forward_origin={
            "type": "hidden_user",
            "date": datetime.now(UTC),
            "sender_user_name": "Other",
        },
    )
    first = from_telegram(message)
    assert first.entities[0].offset == utf16_length("↪️ Переслано от Other\n😀")
    merged = join_text([first, RelayMessage(), RelayMessage("xy", [Entity("italic", 0, 2)])])
    assert merged.entities[-1].offset == utf16_length(first.text + "\n")
    assert merged.text == first.text + "\nxy"


async def test_deletion_note_reply_missing_fallback(storage):
    _, sessions, _ = storage
    bot = NS(
        send_message=AsyncMock(
            side_effect=[
                TelegramBadRequest(
                    method=SendMessage(chat_id=1, text="x"),
                    message="Bad Request: message to be replied not found",
                ),
                NS(message_id=3),
            ]
        )
    )
    tg = TgBot("", NS(inbox_chat_id=1), sessions, bot=bot)
    await tg.deletion_note(20, 30)
    first, second = bot.send_message.call_args_list
    assert first.kwargs["reply_parameters"].message_id == 30
    assert first.kwargs["text"] == "\U0001f5d1\ufe0f\u0020удалено!"
    assert "reply_parameters" not in second.kwargs
    assert second.kwargs["message_thread_id"] == 20


async def test_deletion_note_does_not_hide_other_errors(storage):
    _, sessions, _ = storage
    bot = NS(
        send_message=AsyncMock(
            side_effect=TelegramBadRequest(
                method=SendMessage(chat_id=1, text="x"), message="not enough rights"
            )
        )
    )
    tg = TgBot("", NS(inbox_chat_id=1), sessions, bot=bot)
    with pytest.raises(TelegramBadRequest):
        await tg.deletion_note(20, 30)
    bot.send_message.assert_awaited_once()
