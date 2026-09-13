import ssl
from importlib import resources
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp


class AttachmentUnavailable(ValueError):
    """MAX returned no downloadable URL for this attachment."""


class MediaTooLarge(ValueError):
    def __init__(self, size):
        self.size = size
        super().__init__(f"MAX media exceeds Telegram upload limit: at least {size} bytes")


def oneme_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    ca = resources.files("pymax._data") / "rootca_ssl_rsa2022.crt"
    context.load_verify_locations(cadata=ca.read_text(encoding="utf-8"))
    return context


def is_oneme(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "oneme.ru" or host.endswith(".oneme.ru")


async def download(url: str, dest: Path, limit: int = 50 * 1024 * 1024) -> Path:
    """Проверяет TLS заново на каждом redirect; расширенное доверие только oneme.ru."""
    context = oneme_ssl_context()
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as http:
            for _ in range(6):
                if urlparse(url).scheme != "https":
                    raise ValueError("Media URL must use HTTPS")
                async with http.get(
                    url, ssl=context if is_oneme(url) else True, allow_redirects=False
                ) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        url = urljoin(url, response.headers["Location"])
                        continue
                    response.raise_for_status()
                    if response.content_length and response.content_length > limit:
                        raise MediaTooLarge(response.content_length)
                    size = 0
                    with dest.open("wb") as output:
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            size += len(chunk)
                            if size > limit:
                                raise MediaTooLarge(size)
                            output.write(chunk)
                    return dest
            raise ValueError("Too many media redirects")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
