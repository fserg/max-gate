"""Безопасные причины ошибок: payload и credentials не попадают в Note/журнал."""

from maxgate.diagnostics import exception_message


def reason(exc):
    return exception_message(exc)


def thread_missing(exc):
    return "message thread not found" in str(exc).lower()
