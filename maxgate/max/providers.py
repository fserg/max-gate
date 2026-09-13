import asyncio


class SmsCodeProvider:
    def __init__(self, timeout: float = 60):
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.requested = asyncio.Event()
        self.timeout = timeout

    async def get_code(self, phone: str) -> str:
        self.requested.set()
        try:
            return await asyncio.wait_for(self.queue.get(), self.timeout)
        finally:
            self.requested.clear()


class PasswordProvider:
    def __init__(self, timeout: float = 60):
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.requested = asyncio.Event()
        self.timeout = timeout

    async def get_password(self, hint: str | None = None) -> str:
        self.requested.set()
        try:
            return await asyncio.wait_for(self.queue.get(), self.timeout)
        finally:
            self.requested.clear()


class SavedSessionOnly:
    """max-check не должен даже запрашивать SMS при отсутствии Session."""

    async def authenticate(self, app):
        raise RuntimeError("Saved Session unavailable; SMS login is disabled for max-check")
