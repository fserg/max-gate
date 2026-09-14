"""Чистые модели и правила Relay; смещения Entity всегда в UTF-16."""

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class Entity:
    type: str
    offset: int
    length: int
    url: str | None = None


@dataclass(frozen=True)
class Attachment:
    kind: str
    source: Any = None
    name: str | None = None
    size: int | None = None
    mime: str | None = None
    duration: int | None = None  # milliseconds
    width: int | None = None
    height: int | None = None
    source_chat_id: int | None = None
    source_message_id: int | None = None


@dataclass(frozen=True)
class Note:
    text: str

    def render(self) -> str:
        return f"ℹ️ {self.text}"


@dataclass(frozen=True)
class RelayMessage:
    text: str = ""
    entities: list[Entity] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    reply_to: int | None = None
    sender: str | None = None
    forwarded_from: str | None = None
    notes: list[Note] = field(default_factory=list)


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


ELEMENT_TYPES = {
    "STRONG": "bold",
    "EMPHASIZED": "italic",
    "UNDERLINE": "underline",
    "STRIKETHROUGH": "strikethrough",
    "MONOSPACED": "code",
    "CODE": "pre",
    "LINK": "text_link",
    "HEADING": "bold",
    "QUOTE": "blockquote",
}


def value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def max_entities(text: str, elements) -> list[Entity]:
    result = []
    boundaries = {0}
    offset = 0
    for char in text:
        offset += utf16_length(char)
        boundaries.add(offset)
    for element in elements or []:
        kind = ELEMENT_TYPES.get(value(element, "type"))
        start = value(element, "from_")
        if start is None:
            start = value(element, "from")
        length = value(element, "length")
        # Не угадываем отсутствующие смещения LINK: текст остаётся целым.
        if (
            not kind
            or not isinstance(start, int)
            or not isinstance(length, int)
            or length <= 0
            or start not in boundaries
            or start + length not in boundaries
        ):
            continue
        url = value(value(element, "attributes", {}), "url")
        if kind == "text_link" and not url:
            continue
        result.append(Entity(kind, start, length, url if kind == "text_link" else None))
    return result


def render(message: RelayMessage, chat_type: str, *, owner: bool = False) -> RelayMessage:
    prefix = ""
    entities = []
    sender = "Вы" if owner else message.sender
    if sender and (owner or chat_type.lower() in {"group", "chat", "dialog"}):
        prefix = sender + "\n"
        entities.append(Entity("bold", 0, utf16_length(sender)))
    if message.forwarded_from:
        prefix += f"↪️ Переслано от {message.forwarded_from}\n"
    shift = utf16_length(prefix)
    entities.extend(replace(e, offset=e.offset + shift) for e in message.entities)
    text = prefix + message.text
    if message.notes:
        text += ("\n\n" if text else "") + "\n".join(n.render() for n in message.notes)
    return replace(message, text=text, entities=entities, notes=[])


def split_text(message: RelayMessage, limit: int = 4096) -> list[RelayMessage]:
    """Разбивает без потерь по абзацам, затем строкам, затем границе символа."""
    if limit < 2:
        raise ValueError("Text limit must be at least 2 UTF-16 units")
    if not message.text:
        return [message]
    result = []
    start = 0
    offset = 0
    while start < len(message.text):
        end, units = start, 0
        while end < len(message.text):
            width = utf16_length(message.text[end])
            if units + width > limit:
                break
            units += width
            end += 1
        if end < len(message.text):
            for delimiter in ("\n\n", "\n", " "):
                boundary = message.text.rfind(delimiter, start, end)
                if boundary > start:
                    end = boundary + len(delimiter)
                    break
        text = message.text[start:end]
        size = utf16_length(text)
        entities = []
        for entity in message.entities:
            left = max(offset, entity.offset)
            right = min(offset + size, entity.offset + entity.length)
            if left < right:
                entities.append(replace(entity, offset=left - offset, length=right - left))
        result.append(
            replace(
                message,
                text=text,
                entities=entities,
                attachments=message.attachments if not result else [],
                notes=[],
            )
        )
        start, offset = end, offset + size
    return result


def voice_kind(header: bytes) -> str:
    if (
        header.startswith((b"OggS", b"ID3"))
        or header[4:8] == b"ftyp"
        or (len(header) >= 2 and header[0] == 255 and header[1] & 0xE0 == 0xE0)
    ):
        return "voice"
    return "audio"


TG_ELEMENT_TYPES = {
    "bold": "STRONG",
    "italic": "EMPHASIZED",
    "underline": "UNDERLINE",
    "strikethrough": "STRIKETHROUGH",
    "code": "MONOSPACED",
    "pre": "CODE",
    "text_link": "LINK",
    "url": "LINK",
    "blockquote": "QUOTE",
}


def telegram_entities(text, entities):
    """Normalize supported UTF-16 ranges, resolving URL text before any splitting."""
    boundaries = {0}
    offset = 0
    for char in text:
        offset += utf16_length(char)
        boundaries.add(offset)
    result = []
    for e in entities or []:
        kind, start, length = value(e, "type"), value(e, "offset"), value(e, "length")
        if (
            kind not in TG_ELEMENT_TYPES
            or not isinstance(start, int)
            or not isinstance(length, int)
            or length <= 0
            or start not in boundaries
            or start + length not in boundaries
        ):
            continue
        url = value(e, "url")
        if kind == "url":
            url = text.encode("utf-16-le")[2 * start : 2 * (start + length)].decode("utf-16-le")
            kind = "text_link"
        if kind == "text_link" and not url:
            continue
        result.append(Entity(kind, start, length, url))
    return result


def max_elements(text, entities):
    return [
        dict(
            type=TG_ELEMENT_TYPES[e.type],
            **{"from": e.offset},
            length=e.length,
            **({"attributes": {"url": e.url}} if e.type == "text_link" else {}),
        )
        for e in telegram_entities(text, entities)
    ]


def join_text(messages):
    """Join album captions and translate every entity to the combined UTF-16 offset."""
    text, entities = "", []
    for message in messages:
        if not message.text:
            continue
        if text:
            text += "\n"
        shift = utf16_length(text)
        entities.extend(replace(e, offset=e.offset + shift) for e in message.entities)
        text += message.text
    return RelayMessage(text=text, entities=entities)
