"""One creation path and lock for automatic and Operator-requested Topics."""

import asyncio


async def ensure_topic(store, tg, link_id, locks, *, title_resolver=None):
    async with locks.setdefault(link_id, asyncio.Lock()):
        link = await store.chat(link_id=link_id)
        if link is None:
            raise ValueError("ChatLink not found")
        if link.topic_id is None:
            if title_resolver is not None and link.max_chat_type == "DIALOG":
                title = await title_resolver(link)
                link = await store.change_chat(link.id, max_title=title)
            topic_id = await tg.create_topic(link.max_title or "MAX", link.max_chat_type)
            link = await store.change_chat(link.id, topic_id=topic_id)
        return link
