from dataclasses import replace

from maxgate.domain import Attachment, Note, RelayMessage, telegram_entities, utf16_length


def from_telegram(message) -> RelayMessage:
    text = message.text or message.caption or ""
    entities = telegram_entities(
        text, message.entities if message.text else message.caption_entities
    )
    attachments, notes = [], []
    if message.sticker or message.video_note or message.poll:
        notes.append(Note("⛔ в MAX не переносится"))
    else:
        for kind in ("photo", "video", "document", "audio", "voice"):
            item = getattr(message, kind)
            if not item:
                continue
            if kind == "photo":
                item = item[-1]
            attachments.append(
                Attachment(
                    kind,
                    item.file_id,
                    getattr(item, "file_name", None),
                    item.file_size,
                    getattr(item, "mime_type", None),
                    getattr(item, "duration", 0) * 1000 or None,
                    getattr(item, "width", None),
                    getattr(item, "height", None),
                )
            )
    if message.contact or message.location:
        entities = []
    if message.contact:
        contact = message.contact
        text = f"Контакт: {contact.first_name} {contact.last_name or ''}, {contact.phone_number}"
    if message.location:
        loc = message.location
        text = f"Геолокация: https://maps.google.com/?q={loc.latitude},{loc.longitude}"
    origin = message.forward_origin
    forwarded_from = None
    if origin:
        if origin.type == "user":
            forwarded_from = origin.sender_user.full_name
        elif origin.type == "hidden_user":
            forwarded_from = origin.sender_user_name
        else:
            chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
            forwarded_from = chat.title if chat else "неизвестного отправителя"
        prefix = f"↪️ Переслано от {forwarded_from}\n"
        entities = [replace(e, offset=e.offset + utf16_length(prefix)) for e in entities]
        text = prefix + text
    reply_to = message.reply_to_message.message_id if message.reply_to_message else None
    return RelayMessage(
        text=text,
        entities=entities,
        attachments=attachments,
        reply_to=reply_to,
        forwarded_from=forwarded_from,
        notes=notes,
    )
