"""Безопасные причины ошибок: payload и credentials не попадают в Note/журнал."""

from maxgate.diagnostics import exception_message


def reason(exc):
    return exception_message(exc)


def thread_missing(exc):
    return "message thread not found" in str(exc).lower()


# Only explicit transport/timeout codes are retryable. Unknown server refusals are final.
TRANSIENT_MAX_CODES = frozenset(
    {
        "timeout",
        "error.timeout",
        "error.request.timeout",
        "error.connection.closed",
        "error.connection.lost",
        "error.reconnecting",
    }
)


def max_api_error(exc):
    from pymax.exceptions import ApiError

    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ApiError):
            return exc
        # Only explicit wrapping: implicit context may be an earlier, already handled
        # refusal (e.g. a timeout while trying the alternate emoji representation).
        exc = exc.__cause__
    return None


def permanent_max_error(exc):
    error = max_api_error(exc)
    return error is not None and error.error not in TRANSIENT_MAX_CODES
