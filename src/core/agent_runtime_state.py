"""
Agent 运行时状态管理模块

提供任务注册和线程状态管理：
- TaskRegistry：跟踪和控制活跃的异步任务
- ThreadStateRegistry：管理每个线程的锁、忙碌状态和待处理系统消息计数
"""

import asyncio
from typing import Dict, List, Optional


class TaskRegistry:
    """跟踪和控制活跃异步任务的任务注册表。"""

    def __init__(self):
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._task_lock: Optional[asyncio.Lock] = None

    async def _get_task_lock(self) -> asyncio.Lock:
        """获取或创建任务锁。"""
        if self._task_lock is None:
            self._task_lock = asyncio.Lock()
        return self._task_lock

    async def cancel(self, task_key: str, timeout_seconds: float = 5.0) -> bool:
        """取消指定 task_key 的活跃任务。

        :param task_key: 任务键
        :param timeout_seconds: 取消超时时间
        :return: 如果找到并取消了任务返回 True，否则返回 False
        """
        lock = await self._get_task_lock()
        async with lock:
            task = self._active_tasks.get(task_key)
            actually_cancelled = False
            if task and not task.done():
                task.cancel()
                actually_cancelled = True
                try:
                    await asyncio.wait_for(
                        asyncio.ensure_future(self._wait_task(task)),
                        timeout=timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    # 超时：task 仍未结束，再发一次 cancel 并强制移除，
                    # 避免僵尸协程残留在注册表中。
                    import logging
                    logging.getLogger("TaskRegistry").warning(
                        "Task %s did not finish within %.1fs after cancel; "
                        "sending another cancel and removing from registry.",
                        task_key, timeout_seconds,
                    )
                    task.cancel()
                    # 仍然 fall through 到下面的 pop
                except (asyncio.CancelledError, Exception):
                    pass
            self._active_tasks.pop(task_key, None)
            return actually_cancelled

    @staticmethod
    async def _wait_task(task: asyncio.Task) -> None:
        """等待 task 结束，吞掉其 CancelledError/Exception。

        与直接 await task 的区别：外层 wait_for 超时时可以正常
        cancel 这个 wrapper coroutine，而不会影响原 task 本身
        （原 task 已经在上面收到了 cancel 信号，这里只是旁观等待）。
        """
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    def register(self, task_key: str, task: asyncio.Task) -> None:
        """注册一个任务。"""
        self._active_tasks[task_key] = task

    def unregister(self, task_key: str) -> None:
        """取消注册一个任务。"""
        self._active_tasks.pop(task_key, None)

    def list_keys(self, prefix: str = "") -> List[str]:
        """列出所有任务键，可按前缀过滤。"""
        if not prefix:
            return list(self._active_tasks.keys())
        return [key for key in self._active_tasks.keys() if key.startswith(prefix)]


class ThreadStateRegistry:
    """管理每个线程的锁、忙碌来源和待处理系统消息计数器。"""

    def __init__(self):
        self._thread_locks: Dict[str, asyncio.Lock] = {}
        self._thread_locks_guard: Optional[asyncio.Lock] = None
        self._thread_busy_source: Dict[str, str] = {}
        self._pending_system_messages: Dict[str, int] = {}
        self._thread_context_usage: Dict[str, Dict[str, int]] = {}
        self._thread_last_model: Dict[str, str] = {}

    async def _get_locks_guard(self) -> asyncio.Lock:
        """获取或创建线程锁的守卫锁。"""
        if self._thread_locks_guard is None:
            self._thread_locks_guard = asyncio.Lock()
        return self._thread_locks_guard

    async def get_lock(self, thread_id: str) -> asyncio.Lock:
        """获取指定线程的锁。"""
        guard = await self._get_locks_guard()
        async with guard:
            if thread_id not in self._thread_locks:
                self._thread_locks[thread_id] = asyncio.Lock()
            return self._thread_locks[thread_id]

    def add_pending_system_message(self, thread_id: str) -> None:
        """增加线程的待处理系统消息计数。"""
        self._pending_system_messages[thread_id] = self._pending_system_messages.get(thread_id, 0) + 1

    def consume_pending_system_messages(self, thread_id: str) -> int:
        """消费并返回线程的待处理系统消息计数。"""
        return self._pending_system_messages.pop(thread_id, 0)

    def has_pending_system_messages(self, thread_id: str) -> bool:
        """检查线程是否有待处理的系统消息。"""
        return self._pending_system_messages.get(thread_id, 0) > 0

    def is_thread_busy(self, thread_id: str) -> bool:
        """检查线程是否忙碌（锁被占用）。"""
        lock = self._thread_locks.get(thread_id)
        return lock is not None and lock.locked()

    def set_thread_busy_source(self, thread_id: str, source: str) -> None:
        """设置线程忙碌的来源。"""
        self._thread_busy_source[thread_id] = source

    def clear_thread_busy_source(self, thread_id: str) -> None:
        """清除线程忙碌来源。"""
        self._thread_busy_source.pop(thread_id, None)

    def get_thread_busy_source(self, thread_id: str) -> str:
        """获取线程忙碌的来源。"""
        if not self.is_thread_busy(thread_id):
            return ""
        return self._thread_busy_source.get(thread_id, "unknown")

    def set_thread_context_usage(self, thread_id: str, tokens: int, budget: int) -> None:
        """设置线程当前压缩上下文用量。"""
        tokens = max(0, int(tokens or 0))
        budget = max(0, int(budget or 0))
        percent = min(100, round(tokens / budget * 100)) if budget > 0 else 0
        if tokens > 0 and percent == 0:
            percent = 1
        self._thread_context_usage[thread_id] = {
            "tokens": tokens,
            "budget": budget,
            "percent": percent,
            "remaining": max(0, budget - tokens),
        }

    def get_thread_context_usage(self, thread_id: str) -> Dict[str, int]:
        """获取线程当前压缩上下文用量。"""
        return self._thread_context_usage.get(
            thread_id,
            {"tokens": 0, "budget": 0, "percent": 0, "remaining": 0},
        )

    def set_thread_model(self, thread_id: str, model_name: str) -> None:
        """记录线程上一次推理实际使用的模型名（用于静态路径反推 budget）。"""
        if model_name:
            self._thread_last_model[thread_id] = model_name

    def get_thread_model(self, thread_id: str) -> str:
        """读取线程上一次推理使用的模型名；从未记录时返回空字符串。"""
        return self._thread_last_model.get(thread_id, "")

    def get_all_thread_status(self, prefix: str) -> Dict[str, Dict[str, object]]:
        """获取所有以指定前缀开头的线程状态。"""
        result: Dict[str, Dict[str, object]] = {}
        for thread_id, lock in self._thread_locks.items():
            if not thread_id.startswith(prefix):
                continue
            busy = lock.locked()
            result[thread_id] = {
                "busy": busy,
                "source": self._thread_busy_source.get(thread_id, "") if busy else "",
                "pending_system": self._pending_system_messages.get(thread_id, 0),
            }
        return result
