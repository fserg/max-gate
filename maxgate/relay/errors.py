"""Безопасные причины ошибок: payload и credentials не попадают в Note/журнал."""


def reason(exc):
    kind = type(exc).__name__
    known = {
        "TimeoutError": "истекло время ожидания",
        "ConnectionError": "соединение прервано",
        "TelegramForbiddenError": "боту запрещён доступ к Inbox",
        "MediaTooLarge": "размер файла превышает лимит Telegram",
        "SessionLost": "Session MAX отозвана",
    }
    return known.get(kind, f"ошибка {kind}")


def thread_missing(exc):
    return "message thread not found" in str(exc).lower()
