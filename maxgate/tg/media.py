from dataclasses import replace
from pathlib import Path

from aiogram.types import (
    FSInputFile,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    MessageEntity,
    ReplyParameters,
)

from maxgate.domain import RelayMessage, split_text, utf16_length, voice_kind

UPLOAD_LIMIT = 50 * 1024 * 1024
DOWNLOAD_LIMIT = 20 * 1024 * 1024


class MediaTooLarge(ValueError):
    pass


def entities(items):
    return [MessageEntity(type=e.type, offset=e.offset, length=e.length, url=e.url) for e in items]


class LimitedWriter:
    def __init__(self, output, limit):
        self.output, self.limit, self.size = output, limit, 0

    def write(self, data):
        self.size += len(data)
        if self.size > self.limit:
            raise MediaTooLarge("⛔ Telegram не отдаёт ботам файлы больше 20 МБ")
        return self.output.write(data)

    def seek(self, *args):
        return self.output.seek(*args)

    def flush(self):
        return self.output.flush()


async def download(bot, attachment, dest: Path) -> Path:
    if attachment.size and attachment.size > DOWNLOAD_LIMIT:
        raise MediaTooLarge("⛔ Telegram не отдаёт ботам файлы больше 20 МБ")
    info = await bot.get_file(attachment.source)
    if info.file_size and info.file_size > DOWNLOAD_LIMIT:
        raise MediaTooLarge("⛔ Telegram не отдаёт ботам файлы больше 20 МБ")
    if not info.file_path:
        raise ValueError("Telegram file path missing")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dest.open("wb") as output:
            await bot.download_file(info.file_path, LimitedWriter(output, DOWNLOAD_LIMIT))
        return dest
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


async def send(bot, inbox_chat_id: int, topic_id: int, message: RelayMessage):
    """Отправляет подготовленные локальные вложения. Возвращает все части MessageLink."""
    result = []
    common = dict(chat_id=inbox_chat_id, message_thread_id=topic_id)
    if message.reply_to is not None:
        common["reply_parameters"] = ReplyParameters(
            message_id=message.reply_to, allow_sending_without_reply=True
        )
    text = message.text
    if message.notes:
        text += ("\n\n" if text else "") + "\n".join(n.render() for n in message.notes)
    pending_text = replace(message, text=text, attachments=[], notes=[])
    media = []
    for attachment in message.attachments:
        if attachment.kind in {"contact", "location"}:
            media.append((attachment, None))
            continue
        path = Path(attachment.source)
        if path.stat().st_size > UPLOAD_LIMIT:
            pending_text = replace(
                pending_text,
                text=pending_text.text
                + f"\nℹ️ Файл {attachment.name or path.name}: {path.stat().st_size} байт — больше 50 МБ",
            )
            continue
        if attachment.kind == "voice":
            with path.open("rb") as source:
                attachment = replace(attachment, kind=voice_kind(source.read(16)))
        media.append((attachment, FSInputFile(path, filename=attachment.name)))
    can_caption = media and media[0][0].kind not in {"video_note", "contact", "location"}
    caption = pending_text.text if can_caption and utf16_length(pending_text.text) <= 1024 else None
    caption_entities = entities(pending_text.entities) if caption else None
    if caption:
        pending_text = replace(pending_text, text="", entities=[])
    constructors = {
        "photo": InputMediaPhoto,
        "video": InputMediaVideo,
        "document": InputMediaDocument,
        "audio": InputMediaAudio,
    }
    index = 0
    while index < len(media):
        attachment, file = media[index]
        family = "visual" if attachment.kind in {"photo", "video"} else attachment.kind
        batch = [(attachment, file)]
        if attachment.kind in constructors:
            for other, other_file in media[index + 1 : index + 10]:
                other_family = "visual" if other.kind in {"photo", "video"} else other.kind
                if other_family != family:
                    break
                batch.append((other, other_file))
        if len(batch) > 1:
            items = [
                constructors[a.kind](
                    media=f,
                    parse_mode=None,
                    caption=caption if n == 0 else None,
                    caption_entities=caption_entities if n == 0 else None,
                )
                for n, (a, f) in enumerate(batch)
            ]
            result.extend(await bot.send_media_group(media=items, **common))
        elif attachment.kind in {"contact", "location"}:
            result.append(
                await getattr(bot, f"send_{attachment.kind}")(**attachment.source, **common)
            )
        else:
            kwargs = {attachment.kind: file, **common}
            if attachment.kind != "video_note":
                kwargs.update(caption=caption, caption_entities=caption_entities, parse_mode=None)
            if attachment.duration is not None and attachment.kind in {
                "video",
                "video_note",
                "voice",
                "audio",
            }:
                kwargs["duration"] = attachment.duration // 1000
            result.append(await getattr(bot, f"send_{attachment.kind}")(**kwargs))
        caption, caption_entities = None, None
        index += len(batch)
    if pending_text.text:
        for part in split_text(pending_text):
            result.append(
                await bot.send_message(
                    text=part.text, entities=entities(part.entities), parse_mode=None, **common
                )
            )
    return result
