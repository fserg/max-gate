"""Сообщения ошибок и логи без credentials, HTTP payload и содержимого сообщений."""

import logging
import re
from urllib.parse import urlparse

from pydantic import SecretStr, ValidationError

_secrets: set[str] = set()


def register_secret(value):
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if value:
        _secrets.add(str(value))


def redact(text):
    text = str(text)
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, "<REDACTED>")
    text = re.sub(
        r"https?://[^\s\]\)\"']+",
        lambda m: f"{urlparse(m[0]).scheme}://{urlparse(m[0]).hostname}/<REDACTED>",
        text,
    )
    text = re.sub(
        r"(?is)\b(payload[\w]*|extra_head|headers|body|input_value)\b['\"]?\s*[:=].*",
        r"\1=<REDACTED>",
        text,
    )
    text = re.sub(r"(?i)\b(?:bearer\s+)[^\s,;]+", "Bearer <REDACTED>", text)
    text = re.sub(
        r"(?i)\b(token|password|verifyCode|authorization|phone|secret|code)['\"]?\s*[:=]\s*(?:'[^']*'|\"[^\"]*\"|[^\s,;]+)",
        r"\1=<REDACTED>",
        text,
    )
    return text


def exception_message(exc):
    messages = []
    seen = set()
    while exc is not None and id(exc) not in seen and len(messages) < 4:
        seen.add(id(exc))
        if isinstance(exc, ValidationError):
            detail = "invalid response fields: " + ", ".join(
                ".".join(map(str, e["loc"])) for e in exc.errors(include_input=False)
            )
        else:
            detail = str(exc).strip()
        messages.append(type(exc).__name__ + (f": {detail}" if detail else ""))
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return redact("; caused by ".join(messages))[:1200]


class SafeFormatter(logging.Formatter):
    def format(self, record):
        message = record.getMessage()
        if record.exc_info and record.exc_info[1]:
            message += "; " + exception_message(record.exc_info[1])
        safe = logging.makeLogRecord(
            {
                **record.__dict__,
                "msg": redact(message),
                "args": (),
                "exc_info": None,
                "exc_text": None,
                "stack_info": None,
            }
        )
        return super().format(safe)


class PyMaxHandler(logging.Handler):
    def emit(self, record):
        message = record.getMessage()
        if record.exc_info and record.exc_info[1]:
            message += "; " + exception_message(record.exc_info[1])
        logging.getLogger("maxgate.pymax").log(record.levelno, "%s", redact(message))


def route_pymax_logging():
    logger = logging.getLogger("pymax")
    logger.handlers = [PyMaxHandler()]
    logger.setLevel(logging.WARNING)
    logger.propagate = False
