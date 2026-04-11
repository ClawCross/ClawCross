"""
OASIS Forum - 线程安全的讨论论坛（带持久化）

提供帖子发布、投票、浏览等操作，支持多专家并发访问。
"""

import asyncio
import inspect
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

# 持久化存储目录（相对于项目根目录）
_this_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_this_dir)
DISCUSSIONS_DIR = os.path.join(_project_root, "data", "oasis_discussions")


def coerce_optional_post_id(value) -> int | None:
    """Normalize post id / reply_to from JSON: int or numeric string only.

    Non-numeric strings (e.g. mistaken UUIDs) become None so API models stay valid.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        iv = int(value)
        return iv if iv == value else None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s, 10)
        except ValueError:
            return None
    return None


@dataclass
class TimelineEvent:
    """讨论生命周期中的带时间戳事件"""
    elapsed: float   # 距离讨论开始的秒数
    event: str       # 事件类型，如 "start", "agent_call", "agent_done", "round", "conclude"
    agent: str = ""  # 关联的专家名称（如适用）
    detail: str = "" # 附加信息
    seq: int = 0     # topic 内事件序号，便于增量 GraphRAG 同步

    def to_dict(self) -> dict:
        return {"seq": self.seq, "elapsed": round(self.elapsed, 2), "event": self.event,
                "agent": self.agent, "detail": self.detail}

    @classmethod
    def from_dict(cls, d: dict) -> "TimelineEvent":
        if not isinstance(d, dict):
            d = {}
        allowed = ("elapsed", "event", "agent", "detail", "seq")
        clean = {k: d[k] for k in allowed if k in d}
        clean.setdefault("elapsed", 0.0)
        clean.setdefault("event", "unknown")
        clean.setdefault("agent", "")
        clean.setdefault("detail", "")
        clean.setdefault("seq", 0)
        if clean.get("agent") is None:
            clean["agent"] = ""
        if clean.get("detail") is None:
            clean["detail"] = ""
        if clean.get("event") is None:
            clean["event"] = "unknown"
        return cls(
            elapsed=float(clean["elapsed"]),
            event=str(clean["event"]),
            agent=str(clean["agent"]),
            detail=str(clean["detail"]),
            seq=int(clean["seq"]),
        )


@dataclass
class Post:
    """A single post / reply in a discussion thread."""
    id: int
    author: str
    content: str
    reply_to: int | None = None
    upvotes: int = 0
    downvotes: int = 0
    timestamp: float = field(default_factory=time.time)
    elapsed: float = 0.0    # seconds since discussion started
    voters: dict[str, str] = field(default_factory=dict)  # voter_name -> "up"/"down"
    round_num: int = 0       # round number when this post was published
    source_node_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "author": self.author, "content": self.content,
            "reply_to": self.reply_to, "upvotes": self.upvotes,
            "downvotes": self.downvotes, "timestamp": self.timestamp,
            "elapsed": round(self.elapsed, 2),
            "voters": self.voters,
            "round_num": self.round_num,
            "source_node_id": self.source_node_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Post":
        if not isinstance(d, dict):
            d = {}
        allowed = (
            "id", "author", "content", "reply_to", "upvotes", "downvotes",
            "timestamp", "elapsed", "voters", "round_num", "source_node_id",
        )
        d2 = {k: d[k] for k in allowed if k in d}
        d2.setdefault("reply_to", None)
        d2.setdefault("upvotes", 0)
        d2.setdefault("downvotes", 0)
        d2.setdefault("timestamp", time.time())
        d2.setdefault("elapsed", 0.0)
        d2.setdefault("voters", {})
        d2.setdefault("round_num", 0)
        d2.setdefault("source_node_id", None)
        d2["author"] = "" if d2.get("author") is None else str(d2["author"])
        d2["content"] = "" if d2.get("content") is None else str(d2["content"])
        d2["id"] = int(d2.get("id", 0))
        d2["reply_to"] = coerce_optional_post_id(d2.get("reply_to"))
        if not isinstance(d2.get("voters"), dict):
            d2["voters"] = {}
        return cls(**d2)


@dataclass
class PendingHumanReply:
    """A workflow node currently waiting for a human response."""
    node_id: str
    prompt: str
    author: str
    round_num: int
    reply_to: int | None = None
    submitted_post_id: int | None = None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "prompt": self.prompt,
            "author": self.author,
            "round_num": self.round_num,
            "reply_to": self.reply_to,
            "submitted_post_id": self.submitted_post_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PendingHumanReply":
        return cls(
            node_id=str(d.get("node_id", "")),
            prompt=str(d.get("prompt", "")),
            author=str(d.get("author", "")),
            round_num=int(d.get("round_num", 0)),
            reply_to=coerce_optional_post_id(d.get("reply_to")),
            submitted_post_id=coerce_optional_post_id(d.get("submitted_post_id")),
        )


class DiscussionForum:
    """
    线程安全的单主题共享讨论板。
    所有专家通过此实例并发读写。
    """

    def __init__(self, topic_id: str, question: str, user_id: str = "anonymous", max_rounds: int = 5):
        self.topic_id = topic_id
        self.question = question
        self.user_id = user_id
        self.max_rounds = max_rounds
        self.current_round = 0
        self.posts: list[Post] = []
        self.timeline: list[TimelineEvent] = []
        self.conclusion: str | None = None
        self.status = "pending"
        self.discussion: bool = True    # True=讨论模式, False=执行模式
        self.created_at = time.time()
        self._start_time: float = 0.0   # set when discussion actually starts
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition(self._lock)
        self._counter = 0
        self.pending_human: PendingHumanReply | None = None
        self._waiting_experts: set[str] = set()  # 当前 round 在等待 callback 的 expert 集合
        self.schedule_yaml: str | None = None  # 原始 workflow/schedule，供 world refresh 使用
        self.team: str = ""
        self.swarm_mode: str = ""
        self.swarm: dict[str, Any] | None = None
        self._event_counter = 0
        self._post_hooks: list = []
        self._event_hooks: list = []

    def start_clock(self):
        """标记讨论开始时间（T=0，用于所有 elapsed 计算）"""
        self._start_time = time.time()
        self.log_event("start", detail="Discussion started")

    def elapsed(self) -> float:
        """返回从 start_clock() 起的秒数，未开始则返回 0"""
        if self._start_time <= 0:
            return 0.0
        return time.time() - self._start_time

    def log_event(self, event: str, agent: str = "", detail: str = ""):
        """向时间线追加带时间戳的事件"""
        self._event_counter += 1
        ev = TimelineEvent(
            seq=self._event_counter,
            elapsed=self.elapsed(),
            event=event,
            agent=agent,
            detail=detail,
        )
        self.timeline.append(ev)
        print(f"  [OASIS] ⏱ T+{ev.elapsed:.1f}s  {event}"
              + (f"  [{agent}]" if agent else "")
              + (f"  {detail}" if detail else ""))
        self._dispatch_event_hooks(ev)

    def register_post_hook(self, callback):
        if callback not in self._post_hooks:
            self._post_hooks.append(callback)

    def register_event_hook(self, callback):
        if callback not in self._event_hooks:
            self._event_hooks.append(callback)

    async def _run_post_hooks(self, post: "Post"):
        for callback in list(self._post_hooks):
            try:
                result = callback(self, post)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                print(f"[OASIS] ⚠️ post hook failed: {exc}")

    def _dispatch_event_hooks(self, event: TimelineEvent):
        if not self._event_hooks:
            return

        async def _runner():
            for callback in list(self._event_hooks):
                try:
                    result = callback(self, event)
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    print(f"[OASIS] ⚠️ event hook failed: {exc}")

        try:
            asyncio.get_running_loop().create_task(_runner())
        except RuntimeError:
            return

    # ── 序列化 ──

    def to_dict(self) -> dict:
        return {
            "topic_id": self.topic_id,
            "question": self.question,
            "user_id": self.user_id,
            "max_rounds": self.max_rounds,
            "current_round": self.current_round,
            "posts": [p.to_dict() for p in self.posts],
            "timeline": [e.to_dict() for e in self.timeline],
            "conclusion": self.conclusion,
            "status": self.status,
            "discussion": self.discussion,
            "schedule_yaml": self.schedule_yaml,
            "team": self.team,
            "swarm_mode": self.swarm_mode,
            "swarm": self.swarm,
            "created_at": self.created_at,
            "pending_human": self.pending_human.to_dict() if self.pending_human else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DiscussionForum":
        forum = cls(
            topic_id=d["topic_id"],
            question=d["question"],
            user_id=d.get("user_id", "anonymous"),
            max_rounds=d.get("max_rounds", 5),
        )
        forum.current_round = d.get("current_round", 0)
        forum.conclusion = d.get("conclusion")
        forum.status = d.get("status", "concluded")
        forum.discussion = d.get("discussion", True)
        forum.schedule_yaml = d.get("schedule_yaml")
        forum.team = d.get("team", "")
        forum.swarm_mode = d.get("swarm_mode", "")
        forum.swarm = d.get("swarm")
        forum.created_at = d.get("created_at", 0)
        forum.posts = [Post.from_dict(p) for p in d.get("posts", [])]
        forum.timeline = [TimelineEvent.from_dict(e) for e in d.get("timeline", [])]
        pending_human = d.get("pending_human")
        if isinstance(pending_human, dict) and pending_human.get("node_id"):
            forum.pending_human = PendingHumanReply.from_dict(pending_human)
        forum._counter = max((p.id for p in forum.posts), default=0)
        forum._event_counter = max((e.seq for e in forum.timeline), default=0)
        return forum

    # ── 持久化 ──

    def _storage_path(self) -> str:
        user_dir = os.path.join(DISCUSSIONS_DIR, self.user_id)
        os.makedirs(user_dir, exist_ok=True)
        return os.path.join(user_dir, f"{self.topic_id}.json")

    def save(self):
        """将当前状态持久化到磁盘"""
        path = self._storage_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_all(cls) -> dict[str, "DiscussionForum"]:
        """从磁盘加载所有已持久化的讨论。返回 {topic_id: forum}"""
        result: dict[str, DiscussionForum] = {}
        if not os.path.isdir(DISCUSSIONS_DIR):
            return result
        for user_dir_name in os.listdir(DISCUSSIONS_DIR):
            user_dir = os.path.join(DISCUSSIONS_DIR, user_dir_name)
            if not os.path.isdir(user_dir):
                continue
            for fname in os.listdir(user_dir):
                if not fname.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(user_dir, fname), "r", encoding="utf-8") as f:
                        data = json.load(f)
                    forum = cls.from_dict(data)
                    result[forum.topic_id] = forum
                except Exception as e:
                    print(f"[OASIS] ⚠️ 加载 {fname} 失败: {e}")
        return result

    async def publish(
        self,
        author: str,
        content: str,
        reply_to: int | None = None,
        source_node_id: str | None = None,
    ) -> Post:
        """Publish a new post to the forum (thread-safe)."""
        async with self._changed:
            self._counter += 1
            post = Post(
                id=self._counter,
                author=author,
                content=content,
                reply_to=reply_to,
                elapsed=self.elapsed(),
                round_num=self.current_round,
                source_node_id=source_node_id,
            )
            self.posts.append(post)
            self._changed.notify_all()
            await self._run_post_hooks(post)
            return post

    async def vote(self, voter: str, post_id: int, direction: str):
        """Vote on a post. Each voter can only vote once per post, cannot vote on own posts."""
        async with self._changed:
            post = self._find(post_id)
            if post and voter != post.author and voter not in post.voters:
                post.voters[voter] = direction
                if direction == "up":
                    post.upvotes += 1
                else:
                    post.downvotes += 1
                self._changed.notify_all()

    async def browse(
        self,
        viewer: str | None = None,
        exclude_self: bool = False,
        visible_authors: set[str] | None = None,
        from_round: int | None = None,
    ) -> list[Post]:
        """浏览帖子，支持可选的可见性过滤

        Args:
            viewer: 当前查看者名称
            exclude_self: 是否排除查看者自己的帖子
            visible_authors: 如果设置，只包含这些作者的帖子（执行模式DAG）
            from_round: 如果设置，只包含此轮及之后的帖子（执行模式非DAG）
        """
        async with self._lock:
            result = list(self.posts)
            if exclude_self and viewer:
                result = [p for p in result if p.author != viewer]
            if visible_authors is not None:
                result = [p for p in result if p.author in visible_authors]
            if from_round is not None:
                result = [p for p in result if p.round_num >= from_round]
            return result

    async def get_top_posts(self, n: int = 3) -> list[Post]:
        """获取按净点赞数排名前N的帖子"""
        async with self._lock:
            return sorted(
                self.posts,
                key=lambda p: p.upvotes - p.downvotes,
                reverse=True,
            )[:n]

    async def get_post_count(self) -> int:
        """获取帖子总数"""
        async with self._lock:
            return len(self.posts)

    async def count_posts_by_author(self, author: str, round_num: int | None = None) -> int:
        """Count posts from a specific author, optionally limited to one round."""
        async with self._lock:
            return sum(
                1
                for p in self.posts
                if p.author == author and (round_num is None or p.round_num == round_num)
            )

    async def wait_for_author_post(
        self,
        author: str,
        *,
        round_num: int,
        min_count: int,
        timeout: float,
    ) -> bool:
        """Wait until the author has posted again in the target round or timeout expires."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, 0.0)

        def _predicate() -> bool:
            return sum(
                1
                for p in self.posts
                if p.author == author and p.round_num == round_num
            ) >= min_count

        async with self._changed:
            while True:
                if _predicate():
                    return True
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return False
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return _predicate()

    async def add_waiting_expert(self, author: str) -> None:
        """Expert 开始等待 callback 时加入集合。"""
        async with self._changed:
            self._waiting_experts.add(author)

    async def remove_waiting_expert(self, author: str) -> None:
        """Expert 等待结束（成功或超时）时移除。"""
        async with self._changed:
            self._waiting_experts.discard(author)

    async def is_waiting_expert(self, author: str) -> bool:
        """检查 author 是否在等待集合中。"""
        async with self._changed:
            return author in self._waiting_experts

    async def clear_round_waiting_experts(self) -> None:
        """Round 前进时清除等待集合。"""
        async with self._changed:
            self._waiting_experts.clear()

    async def set_pending_human_reply(
        self,
        *,
        node_id: str,
        prompt: str,
        author: str,
        round_num: int,
        reply_to: int | None = None,
    ) -> None:
        """Register that a human workflow node is waiting for input."""
        async with self._changed:
            self.pending_human = PendingHumanReply(
                node_id=node_id,
                prompt=prompt,
                author=author,
                round_num=round_num,
                reply_to=reply_to,
            )
            self._changed.notify_all()

    async def clear_pending_human_reply(self) -> None:
        """Clear the current pending human node, if any."""
        async with self._changed:
            self.pending_human = None
            self._changed.notify_all()

    async def submit_human_reply(
        self,
        *,
        node_id: str,
        round_num: int,
        content: str,
        author: str,
    ) -> Post:
        """Submit a human reply for the currently waiting workflow node."""
        async with self._changed:
            pending = self.pending_human
            if not pending:
                raise ValueError("No human node is currently waiting for input")
            if pending.node_id != node_id:
                raise ValueError(f"Human node mismatch: waiting for {pending.node_id}, got {node_id}")
            if pending.round_num != round_num:
                raise ValueError(
                    f"Round mismatch: waiting for round {pending.round_num}, got {round_num}"
                )
            self._counter += 1
            post = Post(
                id=self._counter,
                author=author,
                content=content,
                reply_to=pending.reply_to,
                elapsed=self.elapsed(),
                round_num=self.current_round,
                source_node_id=node_id,
            )
            self.posts.append(post)
            pending.submitted_post_id = post.id
            self._changed.notify_all()
            return post

    async def wait_for_human_reply(
        self,
        *,
        node_id: str,
        round_num: int,
        timeout: float,
    ) -> Post | None:
        """Wait until the pending human node receives a reply or times out."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, 0.0)

        async with self._changed:
            while True:
                pending = self.pending_human
                if not pending:
                    return None
                if pending.node_id != node_id or pending.round_num != round_num:
                    return None
                if pending.submitted_post_id is not None:
                    return self._find(pending.submitted_post_id)
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    pending = self.pending_human
                    if (
                        pending
                        and pending.node_id == node_id
                        and pending.round_num == round_num
                        and pending.submitted_post_id is not None
                    ):
                        return self._find(pending.submitted_post_id)
                    return None
    def _find(self, post_id: int) -> Post | None:
        """根据ID查找帖子（调用者须持有锁）"""
        return next((p for p in self.posts if p.id == post_id), None)
