import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pymax import Message

from maxgate.max.client import MaxClient
from maxgate.max.media import is_oneme, oneme_ssl_context
from maxgate.max.providers import PasswordProvider, SavedSessionOnly, SmsCodeProvider


async def test_queue_providers():
    sms, password = SmsCodeProvider(), PasswordProvider()
    waiting = asyncio.create_task(sms.get_code("+1000"))
    await sms.requested.wait()
    await sms.queue.put("fake-code")
    assert await waiting == "fake-code"
    assert not sms.requested.is_set()
    await password.queue.put("fake-password")
    assert await password.get_password() == "fake-password"
    with pytest.raises(TimeoutError):
        await SmsCodeProvider(timeout=0.001).get_code("+1000")
    with pytest.raises(RuntimeError):
        await SavedSessionOnly().authenticate(None)


async def test_lifecycle_and_history():
    handlers = []
    stopped = asyncio.Event()

    async def start():
        try:
            await handlers[0](None)
            await asyncio.Future()
        finally:
            stopped.set()

    client = SimpleNamespace(
        on_start=lambda: handlers.append,
        start=start,
        fetch_history=AsyncMock(return_value=[Message(id=1, time=1, type="USER")]),
    )
    adapter = MaxClient(client)
    await adapter.wait_ready(1)
    assert (await adapter.fetch_history(42))[0].chat_id == 42
    await adapter.stop()
    assert stopped.is_set()
    assert not adapter.ready.is_set()


def test_tls_scope():
    import ssl

    assert is_oneme("https://fd2.oneme.ru/x")
    assert not is_oneme("https://oneme.ru.example.org/x")
    assert not is_oneme("https://fakeoneme.ru/x")
    context = oneme_ssl_context()
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED


async def test_download_redirect_uses_default_trust_for_external_host(monkeypatch, tmp_path):
    import ssl

    from maxgate.max.media import download

    class Response:
        content_length = 3

        def __init__(self, status, headers):
            self.status, self.headers = status, headers
            self.content = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def raise_for_status(self):
            pass

        async def iter_chunked(self, _):
            yield b"abc"

    class Http:
        def __init__(self):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            if len(self.calls) == 1:
                return Response(302, {"Location": "https://example.org/media"})
            return Response(200, {})

    http = Http()
    monkeypatch.setattr("maxgate.max.media.aiohttp.ClientSession", lambda **_: http)
    dest = tmp_path / "media"
    await download("https://fd2.oneme.ru/file", dest)
    assert dest.read_bytes() == b"abc"
    assert isinstance(http.calls[0][1]["ssl"], ssl.SSLContext)
    assert http.calls[1][1]["ssl"] is True
    assert all(not call[1]["allow_redirects"] for call in http.calls)
