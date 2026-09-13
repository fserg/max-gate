"""Исправление HTTP-загрузки File в закреплённом PyMax 2.4.1."""

import asyncio
from urllib.parse import quote, urljoin, urlparse

import aiohttp
from pymax.api.uploads.models import FileUploadResponse
from pymax.api.uploads.payloads import AttachFilePayload, UploadPayload
from pymax.api.uploads.service import UploadService
from pymax.exceptions import UploadError
from pymax.protocol import Opcode

from maxgate.max.media import is_oneme, oneme_ssl_context


class GateUploadService(UploadService):
    ready_timeout = 60

    def __init__(self, original):
        # Dispatcher уже ссылается на original; сохраняем общие таблицы Future.
        self.app = original.app
        self.file_upload_waiters = original.file_upload_waiters
        self.video_upload_waiters = original.video_upload_waiters
        self.voice_upload_waiters = original.voice_upload_waiters

    async def upload_file(self, file):
        try:
            data = await self.app.invoke(Opcode.FILE_UPLOAD, payload=UploadPayload().to_payload())
            info = FileUploadResponse.model_validate(data.payload).info[0]
        except Exception as exc:
            raise UploadError("Failed to request file upload URL") from exc
        size = await file.size()
        headers = {
            "Content-Disposition": f"attachment; filename={quote(file.name)}",
            "Content-Length": str(size),
            "Content-Range": f"0-{size - 1}/{size}",
        }
        future = asyncio.get_running_loop().create_future()
        self.file_upload_waiters[info.file_id] = future
        timeout = aiohttp.ClientTimeout(total=self.app.config.upload_timeout, sock_read=60)
        context = oneme_ssl_context()
        url = info.url
        try:
            async with aiohttp.ClientSession(proxy=self.app.config.proxy, timeout=timeout) as http:
                for _ in range(6):
                    if urlparse(url).scheme != "https":
                        raise UploadError("File upload URL must use HTTPS")
                    async with http.post(
                        url=url,
                        headers=headers,
                        data=file.iter_chunks(1024 * 1024),
                        ssl=context if is_oneme(url) else True,
                        allow_redirects=False,
                    ) as response:
                        if response.status in {307, 308}:
                            url = urljoin(url, response.headers["Location"])
                            continue
                        if response.status != 200:
                            raise UploadError(f"File upload HTTP status {response.status}")
                        try:
                            await asyncio.wait_for(future, self.ready_timeout)
                        except TimeoutError as exc:
                            raise UploadError("Timed out waiting for FILE_READY (60 s)") from exc
                        return AttachFilePayload(file_id=info.file_id)
                raise UploadError("Too many file upload redirects")
        except aiohttp.ClientError as exc:
            raise UploadError("HTTP error during file upload") from exc
        except TimeoutError as exc:
            raise UploadError("File upload HTTP timeout") from exc
        finally:
            self.file_upload_waiters.pop(info.file_id, None)
            if not future.done():
                future.cancel()
