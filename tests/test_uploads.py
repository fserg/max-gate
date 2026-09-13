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
