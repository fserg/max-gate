"""Последовательное исполнение заданий одного ChatLink."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass
class Job:
    run: Callable[[], Awaitable]
    failed: Callable[[Exception], Awaitable]


class ChatQueues:
    def __init__(self, *, sleep=asyncio.sleep, delays=(1, 5, 30)):
        self.sleep, self.delays = sleep, delays
        self.queues: dict[int, asyncio.Queue] = {}
        self.workers: dict[int, asyncio.Task] = {}
        self.closed = False

    def submit(self, chat_link_id: int, job: Job):
        if self.closed:
            raise RuntimeError("Relay is stopping")
        if chat_link_id not in self.queues:
            self.queues[chat_link_id] = asyncio.Queue()
            self.workers[chat_link_id] = asyncio.create_task(self._worker(chat_link_id))
        self.queues[chat_link_id].put_nowait(job)

    async def execute(self, job):
        # Три исполнения с паузами 1/5/30 перед соответствующей попыткой.
        for attempt, delay in enumerate(self.delays):
            await self.sleep(delay)
            while True:
                try:
                    await job.run()
                    return
                except Exception as exc:
                    retry_after = getattr(exc, "retry_after", None)
                    if retry_after is not None:
                        await self.sleep(max(0, retry_after))
                        continue
                    if attempt == len(self.delays) - 1:
                        await job.failed(exc)
                    break

    async def _worker(self, key):
        queue = self.queues[key]
        while True:
            job = await queue.get()
            try:
                await self.execute(job)
            except Exception as exc:
                logging.getLogger("maxgate").error(
                    "Relay failure handler failed (%s)", type(exc).__name__
                )
            finally:
                queue.task_done()

    async def drain(self):
        await asyncio.gather(*(queue.join() for queue in self.queues.values()))

    async def close(self, timeout=30):
        self.closed = True
        try:
            await asyncio.wait_for(self.drain(), timeout)
        except TimeoutError:
            pass
        finally:
            for worker in self.workers.values():
                worker.cancel()
            await asyncio.gather(*self.workers.values(), return_exceptions=True)

    @property
    def size(self):
        return sum(queue.qsize() for queue in self.queues.values())
