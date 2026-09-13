import asyncio
import logging
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from maxgate.domain import RelayMessage, join_text, render, utf16_length
from maxgate.max.media import MediaTooLarge
from maxgate.max.messages import from_max
from maxgate.relay.errors import max_api_error, reason, thread_missing
from maxgate.relay.queue import ChatQueues, Job
from maxgate.relay.storage import RelayStorage
from maxgate.tg.topics import ensure_topic


class RelayEngine:
    def __init__(
        self,
        account,
        sessions,
        max_client,
        tg,
        data_dir,
        event,
        *,
        queues=None,
        clock=time.monotonic,
        inbox_ttl=3600,
        album_delay=1.5,
    ):
        self.account, self.max, self.tg = account, max_client, tg
        self.store = RelayStorage(account.id, sessions)
        self.queues = queues or ChatQueues()
        self.event, self.clock = event, clock
        self.inbox_ttl, self.album_delay = inbox_ttl, album_delay
        self.tmp = Path(data_dir) / "tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.inbox_ready = asyncio.Event()
        self.bound_at = self.clock() if account.inbox_chat_id is not None else None
        if account.inbox_chat_id is not None:
            self.inbox_ready.set()
        self.ingest_lock = asyncio.Lock()
        self.topic_locks = {}
        self.albums = {}
        self.album_captions = {}
        self.chats = {}
        self.last_event_time = None
        self.attachment_signatures = {}
        self.closed = False

    async def inbox_bound(self):
        self.bound_at = self.clock()
        self.inbox_ready.set()

    async def _link(self, chat_id):
        link = await self.store.chat(max_chat_id=chat_id)
        if link:
            return link
        chat = self.chats.get(chat_id) or await self.max.get_chat(chat_id)
        self.chats[chat_id] = chat
        title = await self._title(chat)
        return await self.store.ensure_chat(chat_id, chat.type, title, 0)

    async def _title(self, chat):
        if chat.title:
            return chat.title
        others = [uid for uid in (chat.participants or {}) if int(uid) != self.max.me_id]
        return await self.max.user_name(int(others[0])) if others else f"MAX {chat.id}"

    def _allowed(self, link):
        return not link.muted and (link.max_chat_type != "CHANNEL" or self.account.relay_channels)

    async def _wait_inbox(self, received):
        remaining = self.inbox_ttl - (self.clock() - received)
        if (remaining <= 0 and not self.inbox_ready.is_set()) or (
            self.bound_at is not None and self.bound_at - received > self.inbox_ttl
        ):
            await self.event("Relay discarded: Inbox not bound within one hour", "WARNING")
            return False
        try:
            await asyncio.wait_for(self.inbox_ready.wait(), max(0.001, remaining))
            return True
        except TimeoutError:
            await self.event("Relay discarded: Inbox not bound within one hour", "WARNING")
            return False

    async def _topic(self, link):
        return await ensure_topic(self.store, self.tg, link.id, self.topic_locks)

    async def _send(self, link, message, progress):
        link = await self.store.chat(link_id=link.id)
        link = await self._topic(link)
        try:
            return await self.tg.send(
                link.topic_id, message, progress=progress.setdefault("sent", [])
            )
        except Exception as exc:
            if not thread_missing(exc) or progress.get("recreated"):
                raise
            progress["recreated"] = True
            progress["sent"] = []
            link = await self.store.change_chat(link.id, topic_id=None)
            await self.store.forget_messages(link.id)
            link = await self._topic(link)
            return await self.tg.send(
                link.topic_id, replace(message, reply_to=None), progress=progress["sent"]
            )

    async def note(self, link, text, *, reply_to=None):
        if self.account.inbox_chat_id is None:
            await self.event("Note pending: Inbox is not bound", "WARNING")
            return
        if link is None:
            await self.tg.note(text, reply_to=reply_to)
        else:
            await self._send(link, RelayMessage(text=text, reply_to=reply_to), {})

    def _job(self, link, run, *, reply_to=None, direction="max_to_tg"):
        async def failed(exc):
            safe = reason(exc)
            await self.event(f"Relay failed: {safe}", "ERROR")
            try:
                await self.note(
                    link, ("❌ " if direction == "tg_to_max" else "ℹ️ ") + safe, reply_to=reply_to
                )
            except Exception as note_error:
                await self.event(f"Note failed: {reason(note_error)}", "ERROR")

        return Job(run, failed, direction)

    def _submit(self, link, run, **kwargs):
        self.queues.submit(link.id, self._job(link, run, **kwargs))

    async def accept_max(self, message):
        if self.closed or message.chat_id is None:
            return
        self.last_event_time = message.time
        received = self.clock()
        async with self.ingest_lock:
            link = await self._link(message.chat_id)
            progress = {}

            async def run():
                if await self._wait_inbox(received):
                    await self.max_to_tg(link, message, progress)

            self._submit(link, run)

    async def max_to_tg(self, link, message, progress, *, history=False, timestamp=False):
        link = await self.store.chat(link_id=link.id)
        if not history and not self._allowed(link):
            return
        existing = await self.store.messages(link.id, max_id=message.id)
        if existing and not progress.get("sent"):
            return  # MessageLink покрывает Echo и повторные события/Catch-up.
        sender = await self.max.user_name(message.sender) if message.sender is not None else "?"
        relay = render(
            from_max(message, sender=sender),
            link.max_chat_type,
            owner=message.sender == self.max.me_id,
        )
        if timestamp:
            prefix = datetime.fromtimestamp(message.time / 1000, UTC).strftime("[%d.%m %H:%M] ")
            relay = replace(
                relay,
                text=prefix + relay.text,
                entities=[
                    replace(e, offset=e.offset + utf16_length(prefix)) for e in relay.entities
                ],
            )
        if relay.reply_to is not None:
            replies = await self.store.messages(link.id, max_id=relay.reply_to)
            relay = replace(relay, reply_to=replies[0].tg_message_id if replies else None)
        with TemporaryDirectory(dir=self.tmp) as temp:
            attachments = []
            for n, attachment in enumerate(relay.attachments):
                if attachment.kind in {"location", "contact"}:
                    attachments.append(attachment)
                    continue
                filename = Path(attachment.name or f"attachment-{n}").name
                dest = Path(temp) / f"{n}-{filename}"
                try:
                    await self.max.download_attachment(
                        attachment.source_chat_id or link.max_chat_id,
                        attachment.source_message_id or message.id,
                        attachment.source,
                        dest,
                    )
                except MediaTooLarge as exc:
                    relay = replace(
                        relay,
                        text=relay.text
                        + (
                            f"\nℹ️ Файл {attachment.name or 'без имени'}: "
                            f"не менее {exc.size} байт — больше 50 МБ"
                        ),
                    )
                    continue
                attachments.append(replace(attachment, source=dest))
            relay = replace(relay, attachments=attachments)
            sent = await self._send(link, relay, progress)
        logging.getLogger("maxgate").info(
            "Relay max_to_tg account=%s chat=%s max_id=%s tg_ids=%s history=%s",
            self.account.id,
            link.max_chat_id,
            message.id,
            [m.message_id for m in sent],
            history,
        )
        await self.store.link_messages(
            link.id, message.id, [m.message_id for m in sent], "max_to_tg"
        )
        if not history:
            await self.store.change_chat(link.id, last_relayed_time=message.time)
        self.attachment_signatures[message.id] = self._signature(message)
        if len(self.attachment_signatures) > 1000:
            self.attachment_signatures.pop(next(iter(self.attachment_signatures)))

    @staticmethod
    def _signature(message):
        return tuple(repr(a.model_dump(exclude_none=True)) for a in message.attaches or [])

    async def accept_tg(self, message, relay):
        if self.closed:
            return
        async with self.ingest_lock:
            link = await self.store.chat(topic_id=message.message_thread_id)
            if link is None:
                await self.tg.note(
                    "ℹ️ этот топик не связан с чатом MAX", topic_id=message.message_thread_id
                )
                return
            key = (link.id, message.media_group_id) if message.media_group_id else None
            if key and key in self.albums:
                items = self.albums[key][0]
                if message.message_id not in [m.message_id for m, _ in items]:
                    items.append((message, relay))
                return
            items = [(message, relay)]
            ready_at = self.clock() + (self.album_delay if key else 0)
            progress = {}
            if key:
                self.albums[key] = (items, ready_at)

            async def run():
                if key and key in self.albums:
                    await asyncio.sleep(max(0, ready_at - self.clock()))
                    self.albums.pop(key, None)
                await self.tg_to_max(link, items, progress)

            self._submit(link, run, reply_to=message.message_id, direction="tg_to_max")

    async def tg_to_max(self, link, items, progress):
        items = sorted(items, key=lambda item: item[0].message_id)
        source = items[0][0]
        if await self.store.messages(link.id, tg_id=source.message_id):
            return
        first = items[0][1]
        combined = join_text([relay for _, relay in items])
        attachments = [a for _, relay in items for a in relay.attachments]
        replies = await self.store.messages(link.id, tg_id=first.reply_to) if first.reply_to else []
        relay = replace(
            first,
            text=combined.text,
            entities=combined.entities,
            attachments=attachments,
            reply_to=replies[0].max_message_id if replies else None,
        )
        if "result" not in progress:
            with TemporaryDirectory(dir=self.tmp) as temp:
                downloaded = []
                for n, attachment in enumerate(attachments):
                    dest = Path(temp) / f"{n}-{Path(attachment.name or 'media').name}"
                    # PyMax определяет Photo/Voice по расширению при загрузке.
                    if not dest.suffix:
                        dest = dest.with_suffix(
                            {"photo": ".jpg", "voice": ".ogg", "video": ".mp4"}.get(
                                attachment.kind, ".bin"
                            )
                        )
                    await self.tg.download(attachment, dest)
                    downloaded.append(replace(attachment, source=dest))
                progress["result"] = await self.max.send(
                    link.max_chat_id, replace(relay, attachments=downloaded)
                )
        await self.store.link_messages(
            link.id, progress["result"].id, [m.message_id for m, _ in items], "tg_to_max"
        )

        if len(items) > 1:
            self.album_captions[(link.id, progress["result"].id)] = {
                m.message_id: replace(relay, attachments=[]) for m, relay in items
            }

        logging.getLogger("maxgate").info(
            "Relay tg_to_max account=%s chat=%s max_id=%s tg_ids=%s",
            self.account.id,
            link.max_chat_id,
            progress["result"].id,
            [m.message_id for m, _ in items],
        )

    async def catch_up(self):
        async with self.ingest_lock:
            chats = await self.max.fetch_chats()
            now = int(time.time() * 1000)
            for chat in chats:
                self.chats[chat.id] = chat
                link = await self.store.chat(max_chat_id=chat.id)
                if link is None:
                    await self.store.ensure_chat(chat.id, chat.type, await self._title(chat), now)
                    continue
                if not self._allowed(link) or (chat.last_event_time or 0) <= link.last_relayed_time:
                    continue
                received = self.clock()
                state = {}

                async def run(link=link, received=received, state=state):
                    if not await self._wait_inbox(received):
                        return
                    if "messages" not in state:
                        state["messages"] = sorted(
                            await self.max.fetch_history(
                                link.max_chat_id,
                                from_time=link.last_relayed_time,
                                forward=101,
                                backward=0,
                            ),
                            key=lambda m: (m.time, m.id),
                        )
                    messages = state["messages"]
                    for message in messages[:100]:
                        progress = state.setdefault(message.id, {})
                        if progress.get("done"):
                            continue

                        async def relay_one(message=message, progress=progress):
                            await self.max_to_tg(link, message, progress, timestamp=True)

                        # Execute inline to keep the entire history ahead of live events,
                        # but give each message its own retries and final failure Note.
                        await self.queues.execute(self._job(link, relay_one))
                        progress["done"] = True
                    if len(messages) > 100 and not state.get("noted"):
                        await self.note(
                            link, f"ℹ️ пропущено ещё {len(messages) - 100} (как минимум)"
                        )
                        await self.store.change_chat(link.id, last_relayed_time=messages[-1].time)
                        state["noted"] = True

                self._submit(link, run)

    async def history(self, link, count):
        count = max(1, min(100, count))
        state = {}

        async def run():
            if "messages" not in state:
                state["messages"] = sorted(
                    await self.max.fetch_history(link.max_chat_id, backward=count),
                    key=lambda m: (m.time, m.id),
                )
            for message in state["messages"]:
                progress = state.setdefault(message.id, {})
                if not progress.get("done"):
                    await self.max_to_tg(link, message, progress, history=True, timestamp=True)
                    progress["done"] = True

        self._submit(link, run)

    async def max_edit(self, message):
        if self.closed:
            return
        async with self.ingest_lock:
            if message.chat_id is None:
                return
            link = await self.store.chat(max_chat_id=message.chat_id)
            if link is None:
                return

            progress = []
            noted = False

            async def run():
                nonlocal noted
                links = [
                    row
                    for row in await self.store.messages(link.id, max_id=message.id)
                    if row.direction == "max_to_tg"
                ]
                if not links:
                    return
                current = await self.store.chat(link_id=link.id)
                sender = await self.max.user_name(message.sender) if message.sender else "?"
                relay = render(
                    from_max(message, sender=sender),
                    link.max_chat_type,
                    owner=message.sender == self.max.me_id,
                )
                signature = self._signature(message)
                if not noted and signature != self.attachment_signatures.get(message.id, ()):
                    await self.note(link, "ℹ️ сообщение изменено, вложение обновить нельзя")
                    noted = True
                ids = await self.tg.edit_parts(
                    [row.tg_message_id for row in links],
                    relay,
                    topic_id=current.topic_id,
                    progress=progress,
                )
                if ids:
                    await self.store.link_messages(link.id, message.id, ids, "max_to_tg")
                self.attachment_signatures[message.id] = signature
                logging.getLogger("maxgate").info(
                    "Relay max_to_tg edit account=%s chat=%s max_id=%s tg_ids=%s",
                    self.account.id,
                    link.max_chat_id,
                    message.id,
                    [row.tg_message_id for row in links],
                )

            self._submit(link, run)

    async def max_delete(self, event):
        if self.closed:
            return
        async with self.ingest_lock:
            link = await self.store.chat(max_chat_id=event.chat_id)
            if link is None:
                return

            delivered = set()

            async def run():
                current = await self.store.chat(link_id=link.id)
                for message_id in event.message_ids:
                    if message_id in delivered:
                        continue
                    links = [
                        r
                        for r in await self.store.messages(link.id, max_id=message_id)
                        if r.direction == "max_to_tg"
                    ]
                    if not links:
                        continue
                    await self.tg.deletion_note(current.topic_id, links[0].tg_message_id)
                    delivered.add(message_id)
                    logging.getLogger("maxgate").info(
                        "Relay deletion Note account=%s chat=%s max_id=%s tg_id=%s",
                        self.account.id,
                        link.max_chat_id,
                        message_id,
                        links[0].tg_message_id,
                    )

            self._submit(link, run)

    async def tg_edit(self, message, relay):
        if self.closed:
            return
        async with self.ingest_lock:
            link = await self.store.chat(topic_id=message.message_thread_id)
            if link is None:
                return

            async def run():
                links = await self.store.messages(link.id, tg_id=message.message_id)
                if links and links[0].direction == "tg_to_max":
                    captions = self.album_captions.get((link.id, links[0].max_message_id))
                    combined = relay
                    if captions is not None:
                        captions[message.message_id] = replace(relay, attachments=[])
                        combined = join_text([captions[key] for key in sorted(captions)])
                    await self.max.edit(
                        link.max_chat_id,
                        links[0].max_message_id,
                        combined.text,
                        entities=combined.entities,
                    )
                    logging.getLogger("maxgate").info(
                        "Relay tg_to_max edit account=%s chat=%s max_id=%s tg_id=%s",
                        self.account.id,
                        link.max_chat_id,
                        links[0].max_message_id,
                        message.message_id,
                    )

            self._submit(link, run, reply_to=message.message_id, direction="tg_to_max")

    async def tg_reaction(self, event):
        if self.closed:
            return
        async with self.ingest_lock:
            row = await self.store.message_by_tg(event.message_id)
            if row is None:
                await self.tg.note("⛔ сообщение не связано с MAX", reply_to=event.message_id)
                return
            link = await self.store.chat(link_id=row.chat_link_id)
            if link is None or link.topic_id is None:
                await self.tg.note("⛔ Topic не связан с MAX", reply_to=event.message_id)
                return

            async def failed(exc):
                await self.event(f"Reaction failed: {reason(exc)}", "WARNING")
                text = "⛔ не удалось изменить реакцию в MAX"
                api_error = max_api_error(exc)
                if (
                    api_error is not None
                    and api_error.error
                    in {
                        "error.message.like.unknown.like",
                        "error.message.invalid",
                    }
                    and len(event.new_reaction) == 1
                    and event.new_reaction[0].type == "emoji"
                ):
                    emoji = event.new_reaction[0].emoji
                    try:
                        # Also clears a reaction established before this runner started.
                        await self.max.remove_reaction(link.max_chat_id, row.max_message_id)
                    except Exception as remove_error:
                        await self.event(
                            f"Reaction removal failed: {reason(remove_error)}", "WARNING"
                        )
                        text = f"⛔ MAX не поддерживает реакцию {emoji}, снять реакцию в MAX не удалось"
                    else:
                        text = f"⛔ MAX не поддерживает реакцию {emoji}, реакция в MAX снята"
                await self.tg.note(text, topic_id=link.topic_id, reply_to=event.message_id)

            async def run():
                # MAX keeps one reaction per user. Preserve emoji exactly; server validates it.
                if len(event.new_reaction) > 1 or any(
                    r.type != "emoji" for r in event.new_reaction
                ):
                    raise ValueError("MAX supports one ordinary emoji reaction")
                if event.new_reaction:
                    emoji = event.new_reaction[0].emoji
                    try:
                        await self.max.add_reaction(link.max_chat_id, row.max_message_id, emoji)
                    except Exception as exc:
                        api_error = max_api_error(exc)
                        if api_error is None or api_error.error not in {
                            "error.message.like.unknown.like",
                            "error.message.invalid",
                        }:
                            raise
                        # A single alternate wire representation, not a queue retry.
                        alternate = (
                            emoji.replace("\ufe0f", "") if "\ufe0f" in emoji else emoji + "\ufe0f"
                        )
                        await self.max.add_reaction(link.max_chat_id, row.max_message_id, alternate)
                else:
                    await self.max.remove_reaction(link.max_chat_id, row.max_message_id)

            self.queues.submit(link.id, Job(run, failed, "tg_to_max", once=True), lane="reaction")

    async def chat_update(self, chat):
        if self.closed:
            return
        async with self.ingest_lock:
            self.chats[chat.id] = chat
            link = await self.store.chat(max_chat_id=chat.id)
            if link is None or not chat.title:
                return

            async def run():
                current = await self.store.chat(link_id=link.id)
                if current.max_title == chat.title:
                    return
                if current.topic_id is not None and not current.renamed_by_owner:
                    await self.tg.edit_topic(current.topic_id, chat.title)
                await self.store.change_chat(link.id, max_title=chat.title)

            self._submit(link, run)

    async def topic_edited(self, topic_id, title):
        link = await self.store.chat(topic_id=topic_id)
        if link and title is not None:
            await self.store.change_chat(link.id, renamed_by_owner=True)

    async def command(self, message):
        parts = (message.text or "").split()
        if not parts or not parts[0].startswith("/"):
            return False
        command = parts[0].split("@")[0]
        if command == "/status":
            links = await self.store.chats()
            await self.tg.note(
                f"Session: {self.account.state}; ChatLink: {len(links)}; "
                f"очередь: {self.queues.size}; последнее событие MAX: {self.last_event_time or '—'}",
                topic_id=message.message_thread_id,
            )
            return True
        link = await self.store.chat(topic_id=message.message_thread_id)
        if link is None:
            await self.tg.note(
                "ℹ️ этот топик не связан с чатом MAX", topic_id=message.message_thread_id
            )
        elif command in {"/mute", "/unmute"}:
            await self.store.change_chat(link.id, muted=command == "/mute")
            await self.note(link, "Muted" if command == "/mute" else "Relay включён")
        elif command == "/history":
            try:
                count = int(parts[1]) if len(parts) == 2 else 0
                if count < 1:
                    raise ValueError
            except ValueError:
                await self.note(link, "укажите число сообщений")
                return True
            await self.history(link, count)
        elif command == "/info":
            chat = self.chats.get(link.max_chat_id) or await self.max.get_chat(link.max_chat_id)
            await self.note(
                link,
                f"{link.max_chat_type}: {link.max_title}\n"
                f"Участников: {chat.participants_count}; id: {link.max_chat_id}",
            )
        else:
            await self.note(link, "Команды: /status, /mute, /unmute, /history N, /info")
        return True

    async def close(self, timeout=30):
        self.closed = True
        lost = await self.queues.close(timeout)
        self.albums.clear()
        self.album_captions.clear()
        return lost
