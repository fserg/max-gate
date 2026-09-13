"""Scoped HTTPS uploads for pinned PyMax 2.4.1, preserving its wire payloads."""

import asyncio
import base64
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, quote, urljoin, urlparse

import aiohttp
from pymax.api.uploads.models import FileUploadResponse, PhotoUploadResponse, VideoUploadResponse
from pymax.api.uploads.payloads import (
    AttachFilePayload,
    AttachPhotoPayload,
    UploadPayload,
    VideoAttachPayload,
    VideoNoteAttachPayload,
    VoiceAttachPayload,
)
from pymax.api.uploads.service import UploadService
from pymax.exceptions import UploadError
from pymax.files import VideoNote
from pymax.protocol import Opcode

from maxgate.max.media import is_oneme, oneme_ssl_context


class GateUploadService(UploadService):
    ready_timeout = 60

    def __init__(self, original):
        # Dispatcher callbacks retain original; share their Future tables.
        self.app = original.app
        self.file_upload_waiters = original.file_upload_waiters
        self.video_upload_waiters = original.video_upload_waiters
        self.voice_upload_waiters = original.voice_upload_waiters

    @asynccontextmanager
    async def _post(self, url, body, headers=None):
        timeout = aiohttp.ClientTimeout(total=self.app.config.upload_timeout, sock_read=60)
        context = oneme_ssl_context()
        try:
            async with aiohttp.ClientSession(proxy=self.app.config.proxy, timeout=timeout) as http:
                for _ in range(6):
                    if urlparse(url).scheme != "https":
                        raise UploadError("Upload URL must use HTTPS")
                    async with http.post(
                        url=url,
                        headers=headers,
                        data=body(),
                        ssl=context if is_oneme(url) else True,
                        allow_redirects=False,
                    ) as response:
                        if response.status in {307, 308}:
                            url = urljoin(url, response.headers["Location"])
                            continue
                        if response.status != 200:
                            raise UploadError(f"Upload HTTP status {response.status}")
                        yield response
                        return
                raise UploadError("Too many upload redirects")
        except aiohttp.ClientError as exc:
            raise UploadError("HTTP error during upload") from exc
        except TimeoutError as exc:
            raise UploadError("Upload HTTP timeout") from exc

    async def _info(self, opcode, model, **kwargs):
        try:
            data = await self.app.invoke(opcode, payload=UploadPayload(**kwargs).to_payload())
            return model.model_validate(data.payload).info[0]
        except Exception as exc:
            raise UploadError("Failed to request upload URL") from exc

    async def _ready(self, future, signal):
        try:
            await asyncio.wait_for(future, self.ready_timeout)
        except TimeoutError as exc:
            raise UploadError(f"Timed out waiting for {signal}") from exc

    @staticmethod
    def _cleanup(waiters, key, future):
        waiters.pop(key, None)
        if future is not None and not future.done():
            future.cancel()

    async def upload_file(self, file):
        info = await self._info(Opcode.FILE_UPLOAD, FileUploadResponse)
        size = await file.size()
        headers = {
            "Content-Disposition": f"attachment; filename={quote(file.name)}",
            "Content-Length": str(size),
            "Content-Range": f"0-{size - 1}/{size}",
        }
        future = asyncio.get_running_loop().create_future()
        self.file_upload_waiters[info.file_id] = future
        try:
            async with self._post(info.url, lambda: file.iter_chunks(1024 * 1024), headers):
                await self._ready(future, "FILE_READY")
            return AttachFilePayload(file_id=info.file_id)
        finally:
            self._cleanup(self.file_upload_waiters, info.file_id, future)

    async def upload_photo(self, photo, profile=False):
        data = await self.app.invoke(
            Opcode.PHOTO_UPLOAD, payload=UploadPayload(profile=profile).to_payload()
        )
        try:
            url = data.payload["url"]
            photo_id = parse_qs(urlparse(url).query)["photoIds"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise UploadError("Invalid photo upload URL") from exc
        photo_data = photo.validate_photo()
        if not photo_data:
            raise UploadError("Photo validation failed")
        content = await photo.read()

        def body():
            form = aiohttp.FormData()
            form.add_field(
                "file",
                content,
                filename=f"image.{quote(photo_data[0])}",
                content_type=photo_data[1],
            )
            return form

        async with self._post(url, body) as response:
            result = PhotoUploadResponse.model_validate(await response.json())
        return AttachPhotoPayload(photo_token=result.photos[photo_id].token)

    async def upload_voice(self, voice):
        info = await self._info(Opcode.VIDEO_UPLOAD, VideoUploadResponse, type=2, uploader_type=1)
        size = await voice.size()
        agent = self.app.config.device.user_agent
        user_agent = (
            f"OKMessages/{self.app.config.app_version}"
            f" ({agent.os_version}; {agent.device_name}; {agent.screen})"
        )
        headers = {
            "Content-Disposition": f"attachment; filename={quote(voice.name)}",
            "Content-Range": f"bytes 0-{size - 1}/{size}",
            "Content-Length": str(size),
            "Connection": "keep-alive",
            "Content-Type": "application/octet-stream",
            "User-Agent": quote(user_agent),
        }
        async with self._post(info.url, lambda: voice.iter_chunks(1024 * 1024), headers):
            return VoiceAttachPayload(
                video_id=info.video_id,
                token=info.token,
                duration=await voice.get_duration(),
                wave=b"\x00" * 80,
            )

    async def upload_video(self, video):
        note = isinstance(video, VideoNote)
        info = await self._info(
            Opcode.VIDEO_UPLOAD,
            VideoUploadResponse,
            **({"type": 1, "uploader_type": 1} if note else {}),
        )
        size = await video.size()
        headers = {
            "Content-Disposition": f"attachment; filename={quote(video.name)}",
            "Content-Range": f"bytes 0-{size - 1}/{size}",
            "Content-Length": str(size),
            "Connection": "keep-alive",
        }
        future = None
        if not note:
            future = asyncio.get_running_loop().create_future()
            self.video_upload_waiters[info.video_id] = future
        try:
            async with self._post(info.url, lambda: video.iter_chunks(1024 * 1024), headers) as res:
                if note:
                    data = await res.json(content_type=None)
                    thumb = data.get("thumbhash")
                    thumb = base64.b64decode(thumb + "=" * (-len(thumb) % 4)) if thumb else None
                    return VideoNoteAttachPayload(
                        video_id=info.video_id,
                        token=info.token,
                        thumbhash=thumb,
                        duration=await video.get_duration(),
                    )
                await self._ready(future, "VIDEO_READY")
                return VideoAttachPayload(video_id=info.video_id, token=info.token)
        finally:
            self._cleanup(self.video_upload_waiters, info.video_id, future)
