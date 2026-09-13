import asyncio
import ssl
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from pymax import File
from pymax.api.uploads.service import UploadService
from pymax.exceptions import UploadError


@pytest.mark.parametrize("external", [False, True])
async def test_file_upload_scoped_tls_and_ready(monkeypatch, tmp_path, external):
    from maxgate.max.uploads import GateUploadService

    original = UploadService(
        NS(
            dispatcher=NS(on_internal=lambda _: lambda fn: fn),
            config=NS(proxy=None, upload_timeout=10),
            invoke=AsyncMock(
                return_value=NS(
                    payload={
                        "info": [
                            {
                                "fileId": 42,
                                "token": "fake",
                                "url": "https://fu2.oneme.ru/upload?token=fake",
                            }
                        ]
                    }
                )
            ),
        )
    )
    service = GateUploadService(original)
    contexts = []
    body = []

    class Response:
        def __init__(self, status, headers):
            self.status, self.headers = status, headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    class Http:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def post(self, url, **kwargs):
            contexts.append(kwargs["ssl"])
            assert kwargs["allow_redirects"] is False
            body.append(kwargs["data"])
            if external and len(contexts) == 1:
                return Response(307, {"Location": "https://example.org/upload"})
            assert 42 in original.file_upload_waiters
            asyncio.get_running_loop().call_soon(
                original.file_upload_waiters[42].set_result, NS(file_id=42)
            )
            return Response(200, {})

    monkeypatch.setattr("maxgate.max.uploads.aiohttp.ClientSession", lambda **_: Http())
    path = tmp_path / "file.txt"
    path.write_bytes(b"file test")
    result = await service.upload_file(File(path=path))
    assert result.file_id == 42
    assert isinstance(contexts[0], ssl.SSLContext)
    assert contexts[0].check_hostname and contexts[0].verify_mode == ssl.CERT_REQUIRED
    if external:
        assert contexts[1] is True
    assert original.file_upload_waiters == {}
    assert b"".join([chunk async for chunk in body[-1]]) == b"file test"


async def test_file_ready_timeout_is_distinct_and_waiter_removed(monkeypatch, tmp_path):
    from maxgate.max.uploads import GateUploadService

    original = UploadService(
        NS(
            dispatcher=NS(on_internal=lambda _: lambda fn: fn),
            config=NS(proxy=None, upload_timeout=10),
            invoke=AsyncMock(
                return_value=NS(
                    payload={
                        "info": [
                            {"fileId": 42, "token": "fake", "url": "https://fu2.oneme.ru/upload"}
                        ]
                    }
                )
            ),
        )
    )
    service = GateUploadService(original)
    service.ready_timeout = 0.001

    class Http:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def post(self, **kwargs):
            return self

    monkeypatch.setattr("maxgate.max.uploads.aiohttp.ClientSession", lambda **_: Http())
    path = tmp_path / "file.txt"
    path.write_bytes(b"file test")
    with pytest.raises(UploadError, match="FILE_READY"):
        await service.upload_file(File(path=path))
    assert original.file_upload_waiters == {}


@pytest.mark.parametrize("kind", ["photo", "video", "voice"])
@pytest.mark.parametrize("route", ["success", "http", "downgrade", "external"])
async def test_all_uploads_validate_every_hop(monkeypatch, kind, route):
    from maxgate.max.uploads import GateUploadService

    initial = ("http" if route == "http" else "https") + "://fu2.oneme.ru/u?photoIds=42"
    app = NS(
        dispatcher=NS(on_internal=lambda _: lambda fn: fn),
        config=NS(
            proxy=None,
            upload_timeout=10,
            app_version="test",
            device=NS(user_agent=NS(os_version="os", device_name="device", screen="screen")),
        ),
        invoke=AsyncMock(
            return_value=NS(
                payload={
                    "url": initial,
                    "info": [{"videoId": 42, "url": initial, "token": "fake"}],
                }
            )
        ),
    )
    service = GateUploadService(UploadService(app))
    calls = []
    bodies = []

    class Response:
        def __init__(self, status=200, headers=None):
            self.status, self.headers = status, headers or {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def json(self):
            return {"photos": {"42": {"token": "photo-token"}}}

    class Http(Response):
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            assert kwargs["allow_redirects"] is False
            bodies.append(kwargs["data"])
            if len(calls) == 1 and route in {"downgrade", "external"}:
                scheme = "http" if route == "downgrade" else "https"
                return Response(307, {"Location": f"{scheme}://example.org/u"})
            if kind == "video":
                service.video_upload_waiters[42].set_result(None)
            return Response()

    monkeypatch.setattr("maxgate.max.uploads.aiohttp.ClientSession", lambda **_: Http())

    async def chunks(_):
        yield b"fake-media"

    media = NS(
        name="media",
        size=AsyncMock(return_value=10),
        iter_chunks=chunks,
        validate_photo=lambda: ("png", "image/png"),
        read=AsyncMock(return_value=b"photo"),
        get_duration=AsyncMock(return_value=100),
    )
    operation = getattr(service, "upload_" + kind)
    if route in {"http", "downgrade"}:
        with pytest.raises(UploadError, match="HTTPS"):
            await operation(media)
        assert len(calls) == (0 if route == "http" else 1)
    else:
        result = await operation(media)
        assert result is not None
        assert isinstance(calls[0][1]["ssl"], ssl.SSLContext)
        assert calls[0][1]["ssl"].check_hostname
        assert calls[0][1]["ssl"].verify_mode == ssl.CERT_REQUIRED
        if route == "external":
            assert calls[1][1]["ssl"] is True
            assert bodies[0] is not bodies[1]
    assert not service.video_upload_waiters
    assert not service.voice_upload_waiters
