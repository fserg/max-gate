"""Нормализация содержимого PyMax для Relay."""

from maxgate.domain import Attachment, Note, RelayMessage, max_entities, value


def from_max(message, *, sender: str | None = None, forwarded_from: str | None = None):
    link = message.link
    if link and value(link, "type") == "FORWARD" and value(link, "message"):
        from dataclasses import replace

        original = value(link, "message")
        relay = from_max(
            original,
            sender=sender,
            forwarded_from=forwarded_from or value(link, "chat_name") or "неизвестного отправителя",
        )
        return replace(
            relay,
            reply_to=None,
            attachments=[
                replace(
                    a,
                    source_chat_id=a.source_chat_id or value(link, "chat_id"),
                    source_message_id=a.source_message_id or original.id,
                )
                for a in relay.attachments
            ],
        )
    attachments, notes = [], []
    text = message.text or ""
    for item in message.attaches or []:
        kind = value(item, "type", "UNKNOWN")
        fields = dict(
            source=item,
            name=value(item, "name"),
            size=value(item, "size"),
            duration=value(item, "duration"),
            width=value(item, "width"),
            height=value(item, "height"),
        )
        if kind in {"PHOTO", "FILE", "VIDEO", "AUDIO", "STICKER"}:
            target = {
                "PHOTO": "photo",
                "FILE": "document",
                "VIDEO": "video",
                "AUDIO": "voice",
                "STICKER": "photo",
            }[kind]
            if kind == "VIDEO" and value(item, "video_type") == 1:
                target = "video_note"
            if kind == "STICKER" and (
                value(item, "lottie_url") or value(item, "sticker_type") in {"ANIMATED", "VIDEO"}
            ):
                target = "document"
            if fields["size"] and fields["size"] > 50 * 1024 * 1024:
                notes.append(
                    Note(
                        f"Файл {fields['name'] or 'без имени'}: "
                        f"{fields['size']} байт — больше 50 МБ"
                    )
                )
            else:
                attachments.append(Attachment(target, **fields))
        elif kind == "POLL":
            notes.append(
                Note(
                    "📊 "
                    + (value(item, "title") or "Опрос")
                    + "\n"
                    + "\n".join("• " + value(a, "text", "") for a in value(item, "answers", []))
                )
            )
        elif kind == "CALL":
            status = {"MISSED": "пропущенный", "REJECTED": "отклонённый"}.get(
                value(item, "hangup_type"), "завершённый"
            )
            notes.append(Note(f"📞 {status} звонок, длительность {value(item, 'duration', 0)} мс"))
        elif kind == "CONTROL":
            notes.append(Note(value(item, "title") or value(item, "event", "Системное событие")))
        elif kind == "CONTACT":
            phone = value(item, "phone")
            if phone:
                attachments.append(
                    Attachment(
                        "contact",
                        {
                            "phone_number": phone,
                            "first_name": value(item, "first_name")
                            or value(item, "name")
                            or "Контакт",
                            "last_name": value(item, "last_name"),
                        },
                    )
                )
            else:
                notes.append(
                    Note(
                        "Контакт: "
                        + (value(item, "name") or value(item, "first_name") or "без имени")
                    )
                )
        elif kind == "SHARE":
            url = value(item, "url")
            if url and url not in text:
                text += "\n" + url
        else:
            raw = value(item, "model_extra", {}) or {}
            lat, lon = value(raw, "latitude"), value(raw, "longitude")
            if lat is not None and lon is not None:
                attachments.append(Attachment("location", {"latitude": lat, "longitude": lon}))
            else:
                notes.append(Note(f"Вложение MAX: {kind}"))
                url = value(raw, "url")
                if url:
                    attachments.append(Attachment("document", url))
    link = message.link
    reply_to = None
    if link and value(link, "type") == "REPLY":
        reply_to = value(value(link, "message"), "id")
    if link and value(link, "type") == "FORWARD":
        forwarded_from = forwarded_from or value(link, "chat_name") or "неизвестного отправителя"
    return RelayMessage(
        text,
        max_entities(message.text or "", message.elements),
        attachments,
        reply_to,
        sender,
        forwarded_from,
        notes,
    )
