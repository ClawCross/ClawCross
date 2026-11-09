"""LangGraph checkpoint saver that routes each thread to its own SQLite DB."""

from __future__ import annotations

import asyncio
from pathlib import Path
import random
from typing import Any, AsyncIterator, Iterator, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from utils.checkpoint_paths import (
    DEFAULT_CHECKPOINT_DB_DIR,
    candidate_checkpoint_db_paths_for_thread,
    checkpoint_db_path_for_thread,
    iter_checkpoint_db_paths,
    legacy_hashed_checkpoint_db_path_for_thread,
)


class ThreadRoutedAsyncSqliteSaver(BaseCheckpointSaver):
    """Route LangGraph checkpoint writes to one SQLite file per thread.
    """

    def __init__(
        self,
        checkpoint_dir: str | Path = DEFAULT_CHECKPOINT_DB_DIR,
    ) -> None:
        super().__init__()
        self.checkpoint_dir = Path(checkpoint_dir)
        self._contexts: dict[Path, Any] = {}
        self._savers: dict[Path, AsyncSqliteSaver] = {}
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "ThreadRoutedAsyncSqliteSaver":
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        for ctx in list(self._contexts.values()):
            try:
                await ctx.__aexit__(exc_type, exc_val, exc_tb)
            except Exception:
                pass
        self._contexts.clear()
        self._savers.clear()

    def get_next_version(self, current: str | int | None, channel: None) -> str:
        """Match AsyncSqliteSaver's string version format for LangGraph writes."""
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(str(current).split(".")[0])
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:016}"

    @staticmethod
    def _thread_id(config: RunnableConfig | None) -> str:
        if not config:
            return ""
        configurable = config.get("configurable") or {}
        return str(configurable.get("thread_id") or "")

    async def _saver_for_path(self, path: Path) -> AsyncSqliteSaver:
        resolved = path.resolve()
        saver = self._savers.get(resolved)
        if saver is not None:
            return saver

        async with self._lock:
            saver = self._savers.get(resolved)
            if saver is not None:
                return saver
            resolved.parent.mkdir(parents=True, exist_ok=True)
            ctx = AsyncSqliteSaver.from_conn_string(str(resolved))
            saver = await ctx.__aenter__()
            self._contexts[resolved] = ctx
            self._savers[resolved] = saver
            return saver

    async def _primary_saver(self, config: RunnableConfig) -> AsyncSqliteSaver:
        thread_id = self._thread_id(config)
        path = await self._primary_path_for_thread(thread_id)
        return await self._saver_for_path(path)

    async def _primary_path_for_thread(self, thread_id: str) -> Path:
        preferred = checkpoint_db_path_for_thread(thread_id, self.checkpoint_dir)
        legacy_hashed = legacy_hashed_checkpoint_db_path_for_thread(thread_id, self.checkpoint_dir)
        if preferred.exists() or not legacy_hashed.exists() or preferred == legacy_hashed:
            return preferred

        await self._close_path(legacy_hashed)
        try:
            legacy_hashed.rename(preferred)
            for suffix in ("-wal", "-shm", "-journal"):
                legacy_sidecar = Path(f"{legacy_hashed}{suffix}")
                if legacy_sidecar.exists():
                    legacy_sidecar.rename(Path(f"{preferred}{suffix}"))
        except OSError:
            return legacy_hashed
        return preferred

    async def _existing_saver(self, config: RunnableConfig) -> AsyncSqliteSaver | None:
        thread_id = self._thread_id(config)
        for path in candidate_checkpoint_db_paths_for_thread(self.checkpoint_dir, thread_id):
            return await self._saver_for_path(path)
        return None

    async def _close_path(self, path: Path) -> None:
        resolved = path.resolve()
        async with self._lock:
            saver = self._savers.pop(resolved, None)
            ctx = self._contexts.pop(resolved, None)
        if saver is None or ctx is None:
            return
        await ctx.__aexit__(None, None, None)

    async def aclose_thread(self, thread_id: str) -> None:
        paths = [
            checkpoint_db_path_for_thread(thread_id, self.checkpoint_dir),
            *candidate_checkpoint_db_paths_for_thread(self.checkpoint_dir, thread_id),
        ]
        seen: set[Path] = set()
        for path in paths:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            await self._close_path(path)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        saver = await self._existing_saver(config)
        if saver is None:
            return None
        return await saver.aget_tuple(config)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        primary = await self._primary_saver(config)
        return await primary.aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        primary = await self._primary_saver(config)
        await primary.aput_writes(config, writes, task_id, task_path)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        yielded = 0
        paths: list[Path]
        thread_id = self._thread_id(config)
        if thread_id:
            paths = candidate_checkpoint_db_paths_for_thread(self.checkpoint_dir, thread_id)
        else:
            paths = iter_checkpoint_db_paths(self.checkpoint_dir)

        seen_paths: set[Path] = set()
        for path in paths:
            resolved = path.resolve()
            if resolved in seen_paths or not path.is_file():
                continue
            seen_paths.add(resolved)
            saver = await self._saver_for_path(path)
            async for item in saver.alist(config, filter=filter, before=before, limit=limit):
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return asyncio.run(self.aget_tuple(config))

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return asyncio.run(self.aput(config, checkpoint, metadata, new_versions))

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        asyncio.run(self.aput_writes(config, writes, task_id, task_path))

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        async def collect() -> list[CheckpointTuple]:
            return [
                item
                async for item in self.alist(
                    config,
                    filter=filter,
                    before=before,
                    limit=limit,
                )
            ]

        return iter(asyncio.run(collect()))
