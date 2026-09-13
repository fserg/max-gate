from types import SimpleNamespace

import pytest

from maxgate.domain import (
    Entity,
    Note,
    RelayMessage,
    max_entities,
    render,
    split_text,
    utf16_length,
    voice_kind,
)
from maxgate.max.messages import from_max


def test_utf16_elements_and_missing_offsets():
    elements = [
        SimpleNamespace(type="STRONG", from_=2, length=2),
        SimpleNamespace(type="LINK", length=2),
        {"type": "LINK", "from_": None, "length": 2},
        {"type": "LINK", "from": 0, "length": 2, "attributes": {"url": "https://example.org"}},
        {"type": "STRONG", "from": 1, "length": 1},
        {"type": "STRONG", "from": 0, "length": 100},
    ]
    assert max_entities("😀ab", elements) == [
        Entity("bold", 2, 2),
        Entity("text_link", 0, 2, "https://example.org"),
    ]


@pytest.mark.parametrize(
    "kind,target",
    [
        ("STRONG", "bold"),
        ("EMPHASIZED", "italic"),
        ("UNDERLINE", "underline"),
        ("STRIKETHROUGH", "strikethrough"),
        ("MONOSPACED", "code"),
        ("CODE", "pre"),
        ("HEADING", "bold"),
        ("QUOTE", "blockquote"),
    ],
)
def test_entity_types(kind, target):
    assert max_entities("abc", [{"type": kind, "from": 0, "length": 3}]) == [Entity(target, 0, 3)]


def test_render_sender_forward_note():
    message = RelayMessage(
        "text",
        [Entity("italic", 0, 4)],
        sender="😀 Name",
        forwarded_from="Someone",
        notes=[Note("fallback")],
    )
    rendered = render(message, "group")
    assert rendered.text == "😀 Name\n↪️ Переслано от Someone\ntext\n\nℹ️ fallback"
    assert rendered.entities[0] == Entity("bold", 0, 7)
    assert rendered.entities[1].offset == utf16_length(rendered.text.split("text")[0])
    assert render(message, "dialog").text.startswith("↪️")
    assert render(message, "dialog", owner=True).text.startswith("Вы\n")


@pytest.mark.parametrize(
    "text", ["😀" * 5000, "one\n\ntwo\n\n" * 1000, "abcdef" * 1500, "abc\ndef " * 1000]
)
def test_split_preserves_text_and_entities(text):
    parts = split_text(RelayMessage(text, [Entity("bold", 0, utf16_length(text))]))
    assert "".join(p.text for p in parts) == text
    assert all(utf16_length(p.text) <= 4096 for p in parts)
    assert sum(p.entities[0].length for p in parts) == utf16_length(text)
    assert all(p.entities[0].offset == 0 for p in parts)


def test_split_prefers_paragraph():
    assert [p.text for p in split_text(RelayMessage("abc\n\ndefgh"), 8)] == ["abc\n\n", "defgh"]
    assert split_text(RelayMessage())[0].text == ""


@pytest.mark.parametrize(
    "header,kind",
    [
        (b"OggS123", "voice"),
        (b"ID3xxx", "voice"),
        (b"\x00\x00\x00\x18ftypM4A", "voice"),
        (b"\xff\xfbxx", "voice"),
        (b"RIFF", "audio"),
    ],
)
def test_voice_detection(header, kind):
    assert voice_kind(header) == kind


def test_fallbacks():
    msg = SimpleNamespace(
        text="hi",
        elements=[],
        link=None,
        attaches=[
            {"type": "POLL", "title": "Question?", "answers": [{"text": "Yes"}]},
            {"type": "CALL", "hangup_type": "MISSED", "duration": 1000},
            {"type": "CONTROL", "event": "MEMBER_JOINED"},
            {"type": "UNKNOWN", "model_extra": {"url": "https://example.org/a"}},
            {"type": "LOCATION", "model_extra": {"latitude": 0, "longitude": 1}},
            {"type": "FILE", "name": "big.bin", "size": 51 * 1024 * 1024},
        ],
    )
    relay = from_max(msg)
    assert len(relay.notes) == 5
    assert [a.kind for a in relay.attachments] == ["document", "location"]
    assert "Question?\n• Yes" in relay.notes[0].text


def test_forward_real_message_entities_and_nested_source():
    from pymax import Message

    message = Message(
        id=3,
        time=3,
        type="USER",
        link={
            "type": "FORWARD",
            "chatId": 50,
            "chatName": "source",
            "message": {
                "id": 2,
                "time": 2,
                "type": "USER",
                "text": "inside",
                "elements": [{"type": "STRONG", "from_": 0, "length": 6}],
            },
        },
    )
    relay = render(from_max(message), "DIALOG")
    assert relay.text == "↪️ Переслано от source\ninside"
    assert relay.entities[0].type == "bold"
    assert relay.entities[0].offset == utf16_length("↪️ Переслано от source\n")
    assert relay.entities[0].length == 6
