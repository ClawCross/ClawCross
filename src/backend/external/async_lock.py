"""FIFO asynchronous mutex shared by threads and event loops in this process."""
import asyncio
import collections
import concurrent.futures
import threading


class AsyncLock:
    def __init__(self):
        self._guard = threading.Lock()
        self._owner = None
        self._waiters = collections.deque()

    def locked(self):
        with self._guard:
            return self._owner is not None

    async def __aenter__(self):
        loop = asyncio.get_running_loop()
        token = object()
        future = loop.create_future()
        with self._guard:
            if self._owner is None:
                self._owner = token
                return self
            self._waiters.append((token, loop, future))
        try:
            await future
            return self
        except BaseException:
            with self._guard:
                if self._owner is token:
                    self._release_locked()
                else:
                    self._waiters = collections.deque(w for w in self._waiters if w[0] is not token)
            raise

    def _release_locked(self):
        self._owner = None
        while self._waiters:
            token, loop, future = self._waiters.popleft()
            if loop.is_closed() or future.cancelled():
                continue
            self._owner = token
            def grant(future=future):
                if not future.done():
                    future.set_result(None)
            try:
                loop.call_soon_threadsafe(grant)
                return
            except RuntimeError:
                self._owner = None

    async def __aexit__(self, *_):
        with self._guard:
            self._release_locked()


async def cancel_tasks(tasks):
    """Cancel and join tasks on their owning loops before releasing a session."""
    current = asyncio.current_task()
    waits = []
    for task in set(tasks) - {None, current}:
        if task.done():
            continue
        loop = task.get_loop()
        if loop.is_closed():
            raise RuntimeError('Cannot cancel a task whose event loop has already stopped')
        complete = concurrent.futures.Future()
        def stop(task=task, complete=complete):
            def done(_):
                if not complete.done(): complete.set_result(None)
            if task.done(): done(task)
            else:
                task.add_done_callback(done)
                task.cancel()
        # A callback survives asyncio.run() cancelling its remaining tasks when
        # the cancelled task was that loop's root coroutine. A helper coroutine
        # on that loop would itself be cancelled before it could report success.
        loop.call_soon_threadsafe(stop)
        waits.append(asyncio.wrap_future(complete))
    if waits:
        await asyncio.gather(*waits)
