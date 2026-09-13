"""Plain-text MessageService for pinned PyMax 2.4.1; payload contract preserved."""

from pymax.api.binding import bind_api_model
from pymax.api.messages.enums import MessagePayloadKey
from pymax.api.messages.payloads import (
    DelayedAttributes,
    EditMessagePayload,
    ReplyLink,
    SendMessagePayload,
    SendMessagePayloadMessage,
)
from pymax.api.messages.service import DateTimeUnion, MessageService, SendAttachments
from pymax.api.response import require_payload_item_model, require_payload_model
from pymax.exceptions import ApiError
from pymax.logging import get_logger
from pymax.protocol import Opcode
from pymax.types.domain import Message

logger = get_logger(__name__)


class GateMessageService(MessageService):
    async def send_message(
        self,
        chat_id: int,
        text: str | None = None,
        reply_to: int | None = None,
        attachments: SendAttachments = None,
        *,
        notify: bool = True,
        send_at: DateTimeUnion | None = None,
    ) -> Message:
        logger.info("sending message chat_id=%s text_len=%s", chat_id, len(text) if text else 0)

        if not text and not attachments:
            logger.error("send_message failed: no text or attachments provided")
            raise ValueError("Either text or attachments must be provided")

        clean_text, elements = text, []

        attaches = await self._upload_attachments(attachments)

        frame = SendMessagePayload(
            chat_id=chat_id,
            message=SendMessagePayloadMessage(
                text=clean_text,
                cid=self._next_cid(),
                elements=elements,
                attaches=attaches,
                link=ReplyLink(message_id=reply_to) if reply_to else None,
                delayed_attributes=DelayedAttributes(
                    time_to_fire=self._convert_time(send_at),
                    notify_sender=notify,
                )
                if send_at
                else None,
            ),
            notify=notify,
        )

        try:
            response = await self.app.invoke(Opcode.MSG_SEND, frame.to_payload())
        except ApiError as e:
            if e.error == "attachment.not.ready":
                await self._process_attachment_error(attaches)
                response = await self.app.invoke(Opcode.MSG_SEND, frame.to_payload())
            else:
                raise

        message = bind_api_model(
            self.app,
            require_payload_model(response, Message),
        )
        logger.info("message sent chat_id=%s", chat_id)
        return message

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str | None = None,
        attachments: SendAttachments = None,
    ) -> Message:
        if text is None and not attachments:
            logger.error("edit_message failed: no text or attachments provided")
            raise ValueError("Either text or attachments must be provided")

        clean_text, elements = text, []

        attaches = await self._upload_attachments(attachments)

        frame = EditMessagePayload(
            chat_id=chat_id,
            message_id=message_id,
            text=clean_text,
            elements=elements,
            attachments=attaches,
        )
        try:
            response = await self.app.invoke(Opcode.MSG_EDIT, frame.to_payload())
        except ApiError as e:
            if e.error == "attachment.not.ready":
                await self._process_attachment_error(attaches)

                response = await self.app.invoke(Opcode.MSG_EDIT, frame.to_payload())
            else:
                raise

        message = require_payload_item_model(
            response,
            MessagePayloadKey.MESSAGE,
            Message,
        )
        if message.chat_id is None:
            message.chat_id = chat_id

        return bind_api_model(self.app, message)
