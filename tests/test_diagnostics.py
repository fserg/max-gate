import io
import logging

from pymax.exceptions import UploadError

from maxgate.diagnostics import SafeFormatter, exception_message, redact, register_secret


def test_error_cause_retained_without_payload_and_credentials():
    register_secret("unique-login-credential")
    inner = OSError("certificate verify failed on fu2.oneme.ru")
    outer = UploadError("HTTP upload failed; unique-login-credential")
    outer.__cause__ = inner
    message = exception_message(outer)
    assert "HTTP upload failed" in message
    assert "certificate verify failed on fu2.oneme.ru" in message
    assert "unique-login-credential" not in message
    message = redact("Error at https://fu2.oneme.ru/u?token=secret; payload={private: data}")
    assert "fu2.oneme.ru" in message
    assert "private" not in message and "token=secret" not in message


def test_log_exception_is_sanitized_without_raw_traceback():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SafeFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("test.safe")
    logger.addHandler(handler)
    try:
        try:
            raise ValueError("service denied: password=hunter2; payload={private: data}")
        except ValueError:
            logger.exception("Operation failed")
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert "service denied" in text
    assert "hunter2" not in text and "private" not in text and "Traceback" not in text
