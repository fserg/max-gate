"""Exercise the standalone downloader without importing its logging setup or starting MAX."""

import ast
import logging
import ssl
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import urljoin, urlparse

import pytest

from maxgate.max.media import is_oneme, oneme_ssl_context


@pytest.mark.parametrize("target", ["http://example.org/file", "https://example.org/file"])
async def test_spike_redirect_rechecks_tls(target, tmp_path):
    # Only compile the actual method: the research module has top-level FileHandler setup.
    source = ast.parse(Path("spikes/pymax_smoke.py").read_text())
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == "Spike")
    method = next(
        n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "download"
    )
    calls = []

    class Http:
        headers = {}
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            assert kwargs["allow_redirects"] is False
            self.status = 307 if len(calls) == 1 else 200
            self.headers = {"Location": target} if self.status == 307 else {}
            return self

        def raise_for_status(self):
            assert self.status == 200

        async def read(self):
            return b"fixture"

    scope = dict(
        Path=Path,
        ssl=ssl,
        urljoin=urljoin,
        urlparse=urlparse,
        is_oneme=is_oneme,
        aiohttp=NS(ClientSession=Http),
        log=logging.getLogger("test.spike"),
    )
    exec(compile(ast.Module(body=[method], type_ignores=[]), "<spike-download>", "exec"), scope)
    dest = tmp_path / "media"
    operation = scope["download"](NS(ssl_oneme=oneme_ssl_context()), "https://fu2.oneme.ru/f", dest)
    if target.startswith("http:"):
        with pytest.raises(ValueError, match="HTTPS"):
            await operation
        assert len(calls) == 1 and not dest.exists()
    else:
        await operation
        assert calls[1][1]["ssl"] is True
        assert dest.read_bytes() == b"fixture"
    assert isinstance(calls[0][1]["ssl"], ssl.SSLContext)
