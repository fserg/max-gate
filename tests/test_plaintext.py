from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from pymax.api.messages.service import MessageService
from pymax.formatting.markdown import Formatter
from pymax.protocol import Opcode

from maxgate.max.plaintext import GateMessageService


@pytest.mark.parametrize(
    "text",
    [
        "a_b_c *bold* ~strike~ `code` [label](https://example.org) # >",
        "# heading\n> quote\nC:\\path\\file [ ] _ * ~ ` 😀",
    ],
)
async def test_plain_send_edit_vs_real_pymax_formatter(text):
    requests = []

    async def invoke(opcode, payload):
        requests.append((opcode, payload))
        body = payload["message"] if opcode == Opcode.MSG_SEND else payload
        message = dict(id=1, time=1, type="USER", text=body["text"], elements=body["elements"])
        return NS(payload=message if opcode == Opcode.MSG_SEND else {"message": message})

    app = NS(invoke=invoke, api=NS())
    # Actual upstream service/Formatter demonstrates what Gate must bypass.
    upstream = MessageService(app)
    app.api.messages = upstream
    parsed, entities = Formatter.format_markdown(text)
    result = await upstream.send_message(10, text)
    assert result.text == parsed and len(result.elements) == len(entities)
    assert parsed != text or entities
    service = GateMessageService(app)
    app.api.messages = service
    service._upload_attachments = AsyncMock(return_value=[])
    sent = await service.send_message(10, text, reply_to=99)
    edited = await service.edit_message(10, 1, text)
    assert sent.text == edited.text == text
    assert sent.elements == edited.elements == []
    assert requests[-2][1]["message"]["elements"] == []
    assert requests[-1][1]["elements"] == []
    assert requests[-2][1]["message"]["link"]["messageId"] == 99
    empty = await service.edit_message(10, 1, "")
    assert empty.text == "" and empty.elements == []


def test_readme_uses_relay_term():
    from pathlib import Path

    assert "пересылк" not in Path("README.md").read_text().lower()


async def test_explicit_elements_send_edit_without_markdown():
    from maxgate.domain import Entity, max_elements

    text = "😀 *bold* link"
    elements = max_elements(
        text, [Entity("bold", 3, 6), Entity("text_link", 10, 4, "https://example.org")]
    )
    calls = []

    async def invoke(opcode, payload):
        calls.append(payload)
        body = payload["message"] if opcode == Opcode.MSG_SEND else payload
        msg = dict(id=1, time=1, type="USER", text=body["text"], elements=body["elements"])
        return NS(payload=msg if opcode == Opcode.MSG_SEND else {"message": msg})

    app = NS(invoke=invoke, api=NS())
    service = GateMessageService(app)
    app.api.messages = service
    sent = await service.send_message(10, text, elements=elements)
    edited = await service.edit_message(10, 1, text, elements=elements)
    assert sent.text == edited.text == text
    assert calls[0]["message"]["elements"] == calls[1]["elements"] == elements
