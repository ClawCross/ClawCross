"""
OASIS Forum - FastAPI Server

A standalone discussion forum service where resident expert agents
debate user-submitted questions in parallel.

Start with:
    uvicorn oasis.server:app --host 0.0.0.0 --port 51202
    or
    python -m oasis.server
"""

import os
import platform
import shutil
import subprocess
import sys
import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import httpx
import uvicorn
import aiosqlite
import yaml as _yaml
import json

from dotenv import load_dotenv

# --- Path setup ---
_this_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_this_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

env_path = os.path.join(_project_root, "config", ".env")


def _resolve_openclaw_bin():
    candidates = ["openclaw"]
    if os.name == "nt":
        candidates = ["openclaw.cmd", "openclaw"]

    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    return None
load_dotenv(dotenv_path=env_path)


def _server_host() -> str:
    """Expose services to the Windows host when running inside WSL."""
    explicit_host = os.getenv("CLAWCROSS_SERVER_HOST", "").strip()
    if explicit_host:
        return explicit_host
    return "0.0.0.0" if os.getenv("WSL_DISTRO_NAME") else "127.0.0.1"


def _get_env(key: str, default: str = "") -> str:
    """Read from os.environ first; fall back to .env file if missing.

    configure.py's set_env() writes to .env but does NOT update
    os.environ in *this* process, so a freshly-written value might
    only exist on disk.  Re-read the file as a fallback.
    """
    val = os.getenv(key, "")
    if val:
        return val
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#") and s.startswith(key + "="):
                    return s.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return default

from oasis.models import (
    AgentCallbackRequest,
    CreateTopicRequest,
    HumanReplyRequest,
    HumanWaitInfo,
    ManualPostRequest,
    TopicDetail,
    TopicSummary,
    PostInfo,
    TimelineEventInfo,
    DiscussionStatus,
)
from oasis.forum import DiscussionForum, coerce_optional_post_id
from oasis.agent_catalog import build_agent_catalog
from oasis.engine import DiscussionEngine
from oasis.python_workflow import PythonWorkflowEngine
from oasis.experts import _apply_response
from oasis.swarm_engine import build_pending_swarm, generate_swarm_blueprint
from oasis.openclaw_cli import (
    build_agent_detail as _build_agent_detail_helper,
    fetch_openclaw_channels as _fetch_openclaw_channels_helper,
    fetch_openclaw_full_config as _fetch_openclaw_full_config_helper,
    get_openclaw_default_workspace as _get_openclaw_default_workspace_helper,
    get_openclaw_workspace_path as _get_openclaw_workspace_path_helper,
)

# Ensure src/ is importable for helper reuse
_src_path = os.path.join(_project_root, "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)
try:
    from mcp_servers.oasis import _yaml_to_layout_data
except Exception:
    _yaml_to_layout_data = None


# --- In-memory storage ---
discussions: dict[str, DiscussionForum] = {}
engines: dict[str, DiscussionEngine | PythonWorkflowEngine] = {}
tasks: dict[str, asyncio.Task] = {}

# --- Skills cache ---
_openclaw_skills_cache: dict = {}
_openclaw_managed_skills_dir: str = ""
_openclaw_bundled_skills: list = []


# --- Helpers ---

def _get_forum_or_404(topic_id: str) -> DiscussionForum:
    forum = discussions.get(topic_id)
    if not forum:
        raise HTTPException(404, "Topic not found")
    return forum


def _preload_openclaw_skills():
    """Preload OpenClaw skills information at startup to reduce latency."""
    global _openclaw_skills_cache, _openclaw_managed_skills_dir, _openclaw_bundled_skills
    
    openclaw_bin = _resolve_openclaw_bin()
    if not openclaw_bin:
        print("[OASIS] ⚠️ openclaw CLI not available, skipping skills preload")
        return
    
    try:
        # Execute openclaw skills list --json command
        result = subprocess.run(
            [openclaw_bin, "skills", "list", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        
        if result.returncode != 0:
            print(f"[OASIS] ⚠️ openclaw skills list failed: {result.stderr.strip()[:200]}")
            return
        
        # Parse JSON response
        raw_output = result.stdout
        idx = raw_output.find('{')
        if idx < 0:
            print("[OASIS] ⚠️ Failed to parse openclaw skills list output")
            return
        
        skills_data = json.loads(raw_output[idx:])
        
        # Extract managed skills directory
        _openclaw_managed_skills_dir = skills_data.get("managedSkillsDir", "")
        
        # Extract bundled skills
        all_skills = skills_data.get("skills", [])
        _openclaw_bundled_skills = [
            skill for skill in all_skills 
            if skill.get("source") == "openclaw-bundled"
        ]
        
        # Cache the complete skills data
        _openclaw_skills_cache = skills_data
        
        print(f"[OASIS] ✅ Skills preloaded: {len(all_skills)} total skills, {len(_openclaw_bundled_skills)} bundled skills")
        print(f"[OASIS] 📁 Managed skills directory: {_openclaw_managed_skills_dir}")
        
    except subprocess.TimeoutExpired:
        print("[OASIS] ⚠️ openclaw skills list command timed out")
    except Exception as e:
        print(f"[OASIS] ⚠️ Failed to preload skills: {e}")


def _get_complete_skills_info():
    """Get complete skills information combining all three sources."""
    return {
        "workspace_dir": _get_openclaw_workspace_path(),
        "managed_skills_dir": _openclaw_managed_skills_dir,
        "bundled_skills": _openclaw_bundled_skills,
        "total_skills_count": len(_openclaw_bundled_skills) if _openclaw_bundled_skills else 0,
        "preloaded_at_startup": bool(_openclaw_skills_cache)
    }


def _check_owner(forum: DiscussionForum, user_id: str):
    """Verify the requester owns this discussion."""
    if forum.user_id != user_id:
        raise HTTPException(403, "You do not own this discussion")


# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Preload OpenClaw skills information at startup
    _preload_openclaw_skills()
    
    # Load historical discussions
    loaded = DiscussionForum.load_all()
    discussions.update(loaded)
    print(f"[OASIS] 🏛️ Forum server started (loaded {len(loaded)} historical discussions)")
    yield
    for tid, forum in discussions.items():
        if forum.status == "discussing":
            forum.status = "error"
            forum.conclusion = "服务关闭，讨论被终止"
        forum.save()
    print("[OASIS] 🏛️ Forum server stopped (all discussions saved)")


app = FastAPI(
    title="OASIS Discussion Forum",
    description="Multi-expert parallel discussion service",
    lifespan=lifespan,
)


# ------------------------------------------------------------------
# Background task runner
# ------------------------------------------------------------------
async def _run_discussion(topic_id: str, engine: DiscussionEngine | PythonWorkflowEngine):
    """Run a discussion engine in the background, then fire callback if configured."""
    forum = discussions.get(topic_id)
    try:
        await engine.run()
    except Exception as e:
        print(f"[OASIS] ❌ Topic {topic_id} background error: {e}")
        if forum:
            forum.status = "error"
            forum.conclusion = f"讨论出错: {str(e)}"

    # Upgrade swarm blueprint with discussion results
    if forum and forum.swarm_mode and forum.status in ("concluded",):
        try:
            posts = [
                {"author": p.author, "content": p.content, "upvotes": p.upvotes, "downvotes": p.downvotes}
                for p in await forum.browse()
            ]
            timeline = [
                {"event": e.event, "agent": e.agent, "detail": e.detail, "elapsed": e.elapsed}
                for e in forum.timeline
            ]
            forum.swarm = generate_swarm_blueprint(
                forum.question,
                user_id=forum.user_id,
                team=getattr(engine, "_team", ""),
                schedule_yaml=None,
                posts=posts,
                timeline=timeline,
                conclusion=forum.conclusion or "",
                mode=forum.swarm_mode,
            )
        except Exception as e:
            print(f"[OASIS] ⚠️ Swarm blueprint upgrade failed: {e}")

    if forum:
        forum.save()

    # Fire callback notification
    cb_url = getattr(engine, "callback_url", None)
    if cb_url:
        conclusion = forum.conclusion if forum else "（无结论）"
        status = forum.status if forum else "error"
        cb_session = getattr(engine, "callback_session_id", "default") or "default"
        user_id = forum.user_id if forum else "anonymous"
        internal_token = os.getenv("INTERNAL_TOKEN", "")

        text = (
            f"[OASIS 子任务完成通知]\n"
            f"Topic ID: {topic_id}\n"
            f"状态: {status}\n"
            f"主题: {forum.question if forum else '?'}\n\n"
            f"📋 结论:\n{conclusion}"
        )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    cb_url,
                    json={"user_id": user_id, "text": text, "session_id": cb_session},
                    headers={"X-Internal-Token": internal_token},
                )
            print(f"[OASIS] 📨 Callback sent for {topic_id} → {cb_session}")
        except Exception as cb_err:
            print(f"[OASIS] ⚠️ Callback failed for {topic_id}: {cb_err}")


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.post("/topics", response_model=dict)
async def create_topic(req: CreateTopicRequest):
    """Create a new discussion topic. Returns topic_id for tracking."""
    topic_id = str(uuid.uuid4())[:8]

    forum = DiscussionForum(
        topic_id=topic_id,
        question=req.question,
        user_id=req.user_id,
        max_rounds=req.max_rounds,
    )
    forum.team = req.team or ""
    forum.schedule_yaml = req.schedule_yaml
    discussions[topic_id] = forum
    forum.save()

    try:
        if req.python_file:
            engine = PythonWorkflowEngine(
                forum=forum,
                python_file=req.python_file,
                user_id=req.user_id,
                team=req.team or "",
            )
        else:
            engine = DiscussionEngine(
                forum=forum,
                schedule_yaml=req.schedule_yaml,
                schedule_file=req.schedule_file,
                bot_enabled_tools=req.bot_enabled_tools,
                bot_timeout=req.bot_timeout,
                user_id=req.user_id,
                early_stop=req.early_stop,
                discussion=req.discussion,
                team=req.team or "",
            )
    except Exception as e:
        forum.status = "error"
        forum.conclusion = f"引擎初始化失败: {str(e)}"
        forum.save()
        raise HTTPException(500, f"Engine init failed: {e}")

    engine.callback_url = req.callback_url
    engine.callback_session_id = req.callback_session_id
    engines[topic_id] = engine

    # Generate pending swarm scaffold if requested
    if req.autogen_swarm:
        try:
            forum.swarm_mode = req.swarm_mode or "prediction"
            forum.swarm = build_pending_swarm(
                req.question,
                user_id=req.user_id,
                team=req.team or "",
                schedule_yaml=req.schedule_yaml,
                mode=forum.swarm_mode,
            )
            forum.save()
        except Exception as e:
            print(f"[OASIS] ⚠️ Swarm scaffold generation failed: {e}")

    task = asyncio.create_task(_run_discussion(topic_id, engine))
    tasks[topic_id] = task

    return {
        "topic_id": topic_id,
        "status": "pending",
        "message": f"Discussion started with {len(getattr(engine, 'experts', []))} experts",
    }


@app.delete("/topics/{topic_id}")
async def cancel_topic(topic_id: str, user_id: str = Query(...)):
    """Force-cancel a running discussion."""
    forum = _get_forum_or_404(topic_id)
    _check_owner(forum, user_id)

    if forum.status != "discussing":
        return {"topic_id": topic_id, "status": forum.status, "message": "Discussion already finished"}

    engine = engines.get(topic_id)
    if engine:
        engine.cancel()

    task = tasks.get(topic_id)
    if task and not task.done():
        task.cancel()

    forum.save()
    return {"topic_id": topic_id, "status": "cancelled", "message": "Discussion cancelled"}


@app.post("/topics/{topic_id}/purge")
async def purge_topic(topic_id: str, user_id: str = Query(...)):
    """Permanently delete a discussion record."""
    forum = _get_forum_or_404(topic_id)
    _check_owner(forum, user_id)

    if forum.status in ("pending", "discussing"):
        engine = engines.get(topic_id)
        if engine:
            engine.cancel()
        task = tasks.get(topic_id)
        if task and not task.done():
            task.cancel()

    storage_path = forum._storage_path()
    if os.path.exists(storage_path):
        os.remove(storage_path)

    discussions.pop(topic_id, None)
    engines.pop(topic_id, None)
    tasks.pop(topic_id, None)

    return {"topic_id": topic_id, "message": "Discussion permanently deleted"}


@app.delete("/topics")
async def purge_all_topics(user_id: str = Query(...)):
    """Delete all topics for a specific user."""
    global discussions, engines, tasks

    to_delete = [
        tid for tid, forum in discussions.items()
        if forum.user_id == user_id
    ]

    deleted_count = 0
    for tid in to_delete:
        forum = discussions.get(tid)
        if forum:
            if forum.status in ("pending", "discussing"):
                engine = engines.get(tid)
                if engine:
                    engine.cancel()
                task = tasks.get(tid)
                if task and not task.done():
                    task.cancel()

            storage_path = forum._storage_path()
            if os.path.exists(storage_path):
                os.remove(storage_path)

            discussions.pop(tid, None)
            engines.pop(tid, None)
            tasks.pop(tid, None)
            deleted_count += 1

    return {"deleted_count": deleted_count, "message": f"Deleted {deleted_count} topics"}


@app.post("/topics/{topic_id}/posts", response_model=PostInfo)
async def add_manual_post(topic_id: str, req: ManualPostRequest):
    """Inject a live user post into a running discussion."""
    forum = _get_forum_or_404(topic_id)
    _check_owner(forum, req.user_id)

    task = tasks.get(topic_id)
    engine = engines.get(topic_id)
    if forum.status != "discussing" or not engine or not task or task.done():
        raise HTTPException(409, "Only actively running discussions accept live posts")

    content = (req.content or "").strip()
    if not content:
        raise HTTPException(400, "Post content cannot be empty")

    author = (req.author or req.user_id or "主持人").strip() or "主持人"
    forum.log_event("manual_post", agent=author)
    post = await forum.publish(author=author[:80], content=content, reply_to=req.reply_to)
    forum.save()

    return PostInfo(
        id=post.id,
        author=post.author,
        content=post.content,
        reply_to=post.reply_to,
        upvotes=post.upvotes,
        downvotes=post.downvotes,
        timestamp=post.timestamp,
        elapsed=post.elapsed,
    )


@app.post("/topics/{topic_id}/callback", response_model=dict)
async def add_agent_callback(topic_id: str, req: AgentCallbackRequest):
    """Apply a structured OASIS callback submitted by an agent itself."""
    forum = _get_forum_or_404(topic_id)
    _check_owner(forum, req.user_id)

    task = tasks.get(topic_id)
    engine = engines.get(topic_id)
    if forum.status != "discussing" or not engine or not task or task.done():
        raise HTTPException(409, "Only actively running discussions accept agent callbacks")
    if req.round_num != forum.current_round:
        raise HTTPException(
            409,
            f"Round mismatch: callback for round {req.round_num}, current round is {forum.current_round}",
        )

    author = (req.author or "").strip()
    if not author:
        raise HTTPException(400, "Author cannot be empty")
    if not await forum.is_waiting_expert(author):
        raise HTTPException(
            409,
            f"Author {author} is not waiting for callback in round {req.round_num}",
        )
    if not isinstance(req.result, dict) or not req.result:
        raise HTTPException(400, "Callback result must be a non-empty object")

    others = await forum.browse(viewer=author, exclude_self=True)
    await _apply_response(req.result, author[:200], forum, others)
    forum.log_event(
        "agent_callback",
        agent=author[:200],
        detail=f"round={req.round_num}, type={req.result.get('clawcross_type', 'oasis reply')}",
    )
    forum.save()

    posts = await forum.browse()
    latest = posts[-1] if posts else None
    return {
        "status": "applied",
        "topic_id": topic_id,
        "author": author[:200],
        "round_num": req.round_num,
        "clawcross_type": req.result.get("clawcross_type", "oasis reply"),
        "post_id": latest.id if latest and latest.author == author[:200] else None,
    }


@app.post("/topics/{topic_id}/human-reply", response_model=PostInfo)
async def add_human_reply(topic_id: str, req: HumanReplyRequest):
    """Submit a plain-text human reply for a waiting workflow node."""
    forum = _get_forum_or_404(topic_id)
    _check_owner(forum, req.user_id)

    task = tasks.get(topic_id)
    engine = engines.get(topic_id)
    if forum.status != "discussing" or not engine or not task or task.done():
        raise HTTPException(409, "Only actively running discussions accept human workflow replies")

    try:
        post = await forum.submit_human_reply(
            node_id=req.node_id,
            round_num=req.round_num,
            content=req.content.strip(),
            author=(req.author or req.user_id or "主持人").strip() or "主持人",
        )
    except ValueError as e:
        raise HTTPException(409, str(e))

    forum.save()
    return PostInfo(
        id=post.id,
        author=post.author,
        content=post.content,
        reply_to=post.reply_to,
        upvotes=post.upvotes,
        downvotes=post.downvotes,
        timestamp=post.timestamp,
        elapsed=post.elapsed,
    )


def _coerce_discussion_status(raw: str) -> DiscussionStatus:
    try:
        return DiscussionStatus(raw)
    except ValueError:
        return DiscussionStatus.ERROR


def _sanitize_swarm_for_api(obj):
    """Ensure swarm dict is JSON-serializable (no datetime / odd types breaking OpenAPI)."""
    if obj is None:
        return None
    try:
        return json.loads(json.dumps(obj, default=str))
    except (TypeError, ValueError):
        return {"_error": "swarm payload could not be normalized"}


def _build_topic_detail(forum: DiscussionForum, posts: list) -> TopicDetail:
    """Build TopicDetail with defensive coercion so bad persisted data cannot 500 the API."""
    return TopicDetail(
        topic_id=str(forum.topic_id),
        question=str(forum.question or ""),
        user_id=str(forum.user_id or "anonymous"),
        status=_coerce_discussion_status(str(forum.status or "error")),
        current_round=int(forum.current_round or 0),
        max_rounds=int(forum.max_rounds or 5),
        posts=[
            PostInfo(
                id=int(getattr(p, "id", 0) or 0),
                author=str(getattr(p, "author", None) or ""),
                content=str(getattr(p, "content", None) or ""),
                reply_to=coerce_optional_post_id(getattr(p, "reply_to", None)),
                upvotes=int(getattr(p, "upvotes", 0) or 0),
                downvotes=int(getattr(p, "downvotes", 0) or 0),
                timestamp=float(getattr(p, "timestamp", 0) or 0),
                elapsed=float(getattr(p, "elapsed", 0) or 0),
            )
            for p in posts
        ],
        timeline=[
            TimelineEventInfo(
                elapsed=float(getattr(e, "elapsed", 0) or 0),
                event=str(getattr(e, "event", None) or "unknown"),
                agent=str(getattr(e, "agent", None) or ""),
                detail=str(getattr(e, "detail", None) or ""),
            )
            for e in forum.timeline
        ],
        discussion=bool(forum.discussion),
        conclusion=forum.conclusion,
        swarm_mode=forum.swarm_mode or None,
        swarm=_sanitize_swarm_for_api(forum.swarm),
        pending_human=(
            HumanWaitInfo(
                node_id=str(forum.pending_human.node_id),
                prompt=str(forum.pending_human.prompt or ""),
                author=str(forum.pending_human.author or ""),
                round_num=int(forum.pending_human.round_num or 0),
                reply_to=coerce_optional_post_id(forum.pending_human.reply_to),
            )
            if forum.pending_human
            else None
        ),
    )


@app.get("/topics/{topic_id}", response_model=TopicDetail)
async def get_topic(topic_id: str, user_id: str = Query(...)):
    """Get full discussion detail."""
    forum = _get_forum_or_404(topic_id)
    _check_owner(forum, user_id)

    posts = await forum.browse()
    try:
        return _build_topic_detail(forum, posts)
    except Exception as exc:
        print(f"[OASIS] ❌ get_topic serialize failed topic_id={topic_id}: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to serialize topic (check discussion JSON on disk): {exc}",
        ) from exc


@app.get("/topics/{topic_id}/stream")
async def stream_topic(topic_id: str, user_id: str = Query(...)):
    """SSE stream for real-time discussion updates."""
    forum = _get_forum_or_404(topic_id)
    _check_owner(forum, user_id)

    async def event_generator():
        last_count = 0
        last_round = 0
        last_timeline_idx = 0      # 已发送的 timeline 事件索引

        while forum.status in ("pending", "discussing"):
            if forum.discussion:
                # ── 讨论模式：原有逻辑，按帖子轮询 ──
                posts = await forum.browse()

                if forum.current_round > last_round:
                    last_round = forum.current_round
                    yield f"data: 📢 === 第 {last_round} 轮讨论 ===\n\n"

                if len(posts) > last_count:
                    for p in posts[last_count:]:
                        prefix = f"↳回复#{p.reply_to}" if p.reply_to else "📌"
                        yield (
                            f"data: {prefix} [{p.author}] "
                            f"(👍{p.upvotes}): {p.content}\n\n"
                        )
                    last_count = len(posts)
            else:
                # ── 执行模式：timeline 事件当普通消息发送 ──
                tl = forum.timeline

                while last_timeline_idx < len(tl):
                    ev = tl[last_timeline_idx]
                    last_timeline_idx += 1

                    if ev.event == "start":
                        yield f"data: 🚀 执行开始\n\n"
                    elif ev.event == "round":
                        yield f"data: 📢 {ev.detail}\n\n"
                    elif ev.event == "agent_call":
                        yield f"data: ⏳ {ev.agent} 开始执行...\n\n"
                    elif ev.event == "agent_done":
                        yield f"data: ✅ {ev.agent} 执行完成\n\n"
                    elif ev.event == "conclude":
                        yield f"data: 🏁 执行完成\n\n"

            await asyncio.sleep(1)

        if forum.discussion:
            if forum.conclusion:
                yield f"data: \n🏆 === 讨论结论 ===\n{forum.conclusion}\n\n"
        else:
            yield f"data: ✅ 已完成\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/topics", response_model=list[TopicSummary])
async def list_topics(user_id: str = Query(...)):
    """List discussion topics for a specific user."""
    items = []
    for f in discussions.values():
        if f.user_id != user_id:
            continue
        items.append(
            TopicSummary(
                topic_id=f.topic_id,
                question=f.question,
                user_id=f.user_id,
                status=DiscussionStatus(f.status),
                post_count=len(f.posts),
                current_round=f.current_round,
                max_rounds=f.max_rounds,
                created_at=f.created_at,
                swarm_mode=f.swarm_mode or None,
                has_swarm=f.swarm is not None,
            )
        )
    items.sort(key=lambda x: x.created_at, reverse=True)
    return items


@app.post("/topics/{topic_id}/swarm/refresh")
async def refresh_swarm(topic_id: str, user_id: str = Query("default")):
    """Regenerate the swarm blueprint using current discussion data."""
    forum = _get_forum_or_404(topic_id)
    _check_owner(forum, user_id)

    mode = forum.swarm_mode or "prediction"
    try:
        posts = [
            {"author": p.author, "content": p.content, "upvotes": p.upvotes, "downvotes": p.downvotes}
            for p in await forum.browse()
        ]
        timeline = [
            {"event": e.event, "agent": e.agent, "detail": e.detail, "elapsed": e.elapsed}
            for e in forum.timeline
        ]
        forum.swarm = generate_swarm_blueprint(
            forum.question,
            user_id=forum.user_id,
            team="",
            posts=posts,
            timeline=timeline,
            conclusion=forum.conclusion or "",
            mode=mode,
        )
        forum.save()
    except Exception as e:
        raise HTTPException(500, f"Swarm refresh failed: {e}")

    return {"topic_id": topic_id, "status": "ok", "swarm": forum.swarm}


@app.get("/topics/{topic_id}/conclusion")
async def get_conclusion(topic_id: str, user_id: str = Query(...), timeout: int = 300):
    """Get the final conclusion (blocks until discussion finishes)."""
    forum = _get_forum_or_404(topic_id)
    _check_owner(forum, user_id)

    elapsed = 0
    while forum.status not in ("concluded", "error") and elapsed < timeout:
        await asyncio.sleep(1)
        elapsed += 1

    if forum.status == "error":
        raise HTTPException(500, f"Discussion failed: {forum.conclusion}")
    if forum.status != "concluded":
        # Execution mode: return 202 (still running) instead of 504 error
        if not forum.discussion:
            return {
                "topic_id": topic_id,
                "question": forum.question,
                "status": "running",
                "current_round": forum.current_round,
                "total_posts": len(forum.posts),
                "message": "执行仍在后台运行中，可稍后通过 check_oasis_discussion 查看结果",
            }
        raise HTTPException(504, "Discussion timed out")

    return {
        "topic_id": topic_id,
        "question": forum.question,
        "conclusion": forum.conclusion,
        "rounds": forum.current_round,
        "total_posts": len(forum.posts),
    }


# ------------------------------------------------------------------
# Expert persona CRUD
# ------------------------------------------------------------------

@app.get("/experts")
async def list_experts(user_id: str = "", team: str = ""):
    """List all available expert agents (public + agency + user custom + team)."""
    from oasis.experts import get_all_experts
    configs = get_all_experts(user_id or None, team=team)
    result = []
    for c in configs:
        persona_raw = c["persona"]
        # Agency 专家的 persona 是完整 md 正文，过长时截断为预览
        if len(persona_raw) > 300:
            persona_preview = persona_raw[:300] + "..."
        else:
            persona_preview = persona_raw
        entry = {
            "name": c["name"],
            "tag": c["tag"],
            "persona": persona_preview,
            "source": c.get("source", "public"),
            "deletable": c.get("source", "public") not in {"public", "agency"},
        }
        # 双语名称：公共专家有 name_en，agency 专家有 name_zh
        if c.get("name_zh"):
            entry["name_zh"] = c["name_zh"]
        if c.get("name_en"):
            entry["name_en"] = c["name_en"]
        # 为 agency 专家附加分类和描述
        if c.get("category"):
            entry["category"] = c["category"]
        if c.get("description"):
            entry["description"] = c["description"]
        result.append(entry)
    return {"experts": result}


@app.get("/sessions/oasis")
async def list_oasis_sessions(user_id: str = Query("")):
    """List all oasis-managed sessions by scanning the agent checkpoint DB.

    Query param: user_id (optional). If provided, only sessions for that user are returned.
    """
    db_path = os.path.join(_project_root, "data", "agent_memory.db")
    if not os.path.exists(db_path):
        return {"sessions": []}

    prefix = f"{user_id}#" if user_id else None
    sessions = []
    try:
        async with aiosqlite.connect(db_path) as db:
            if prefix:
                cursor = await db.execute(
                    "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ? ORDER BY thread_id",
                    (f"{prefix}%#oasis%",),
                )
            else:
                cursor = await db.execute(
                    "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ? ORDER BY thread_id",
                    (f"%#oasis%",),
                )
            rows = await cursor.fetchall()
            for (thread_id,) in rows:
                # thread_id format: "user#session_id"
                if "#" in thread_id:
                    user_part, sid = thread_id.split("#", 1)
                else:
                    user_part = ""
                    sid = thread_id
                tag = sid.split("#")[0] if "#" in sid else sid

                # get latest checkpoint message count
                ckpt_cursor = await db.execute(
                    "SELECT type, checkpoint FROM checkpoints WHERE thread_id = ? ORDER BY ROWID DESC LIMIT 1",
                    (thread_id,),
                )
                ckpt_row = await ckpt_cursor.fetchone()
                msg_count = 0
                if ckpt_row:
                    try:
                        # Try to decode JSON-like checkpoint; conservative approach
                        ckpt_blob = ckpt_row[1]
                        if isinstance(ckpt_blob, (bytes, bytearray)):
                            ckpt_blob = ckpt_blob.decode('utf-8', errors='ignore')
                        ckpt_data = json.loads(ckpt_blob) if isinstance(ckpt_blob, str) else {}
                        messages = ckpt_data.get("channel_values", {}).get("messages", [])
                        msg_count = len(messages)
                    except Exception:
                        msg_count = 0

                sessions.append({
                    "user_id": user_part,
                    "session_id": sid,
                    "tag": tag,
                    "message_count": msg_count,
                })
    except Exception as e:
        raise HTTPException(500, f"扫描 session 失败: {e}")

    return {"sessions": sessions}


class WorkflowSaveRequest(BaseModel):
    user_id: str
    name: str
    schedule_yaml: str
    description: str = ""
    save_layout: bool = False  # deprecated, layout is now generated on-the-fly from YAML
    team: str = ""  # Team name for scoped workflow storage


def _workflow_yaml_dir(user_id: str, team: str = "") -> str:
    """Return the YAML workflow directory path (team-scoped when team is provided)."""
    if team:
        return os.path.join(_project_root, "data", "user_files", user_id, "teams", team, "oasis", "yaml")
    return os.path.join(_project_root, "data", "user_files", user_id, "oasis", "yaml")


@app.post("/workflows")
async def save_workflow(req: WorkflowSaveRequest):
    """Save a YAML workflow under data/user_files/{user}/[teams/{team}/]oasis/yaml/."""
    user = req.user_id
    name = req.name
    if not name.endswith((".yaml", ".yml")):
        name += ".yaml"

    # validate YAML
    try:
        data = _yaml.safe_load(req.schedule_yaml)
        if not isinstance(data, dict) or "plan" not in data:
            raise ValueError("must contain 'plan'")
    except Exception as e:
        raise HTTPException(400, f"YAML 解析失败: {e}")

    yaml_dir = _workflow_yaml_dir(user, req.team)
    os.makedirs(yaml_dir, exist_ok=True)
    filepath = os.path.join(yaml_dir, name)
    content = (f"# {req.description}\n" if req.description else "") + req.schedule_yaml
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(500, f"保存失败: {e}")

    return {"status": "ok", "file": name, "path": filepath}


@app.get("/workflows")
async def list_workflows(user_id: str = Query(...), team: str = Query("")):
    yaml_dir = _workflow_yaml_dir(user_id, team)
    if not os.path.isdir(yaml_dir):
        return {"workflows": []}
    files = sorted(f for f in os.listdir(yaml_dir) if f.endswith((".yaml", ".yml")))
    items = []
    for fname in files:
        fpath = os.path.join(yaml_dir, fname)
        desc = ""
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                first = f.readline().strip()
                if first.startswith("#"):
                    desc = first.lstrip("# ")
        except Exception:
            pass
        items.append({"file": fname, "description": desc})
    return {"workflows": items}


@app.get("/agents/catalog")
async def list_agent_catalog(user_id: str = Query(...), team: str = Query("")):
    return {"agents": build_agent_catalog(user_id, team)}


class LayoutFromYamlRequest(BaseModel):
    user_id: str
    yaml_source: str
    layout_name: str = ""
    team: str = ""  # Team name for scoped workflow lookup


@app.post("/layouts/from-yaml")
async def layouts_from_yaml(req: LayoutFromYamlRequest):
    """Generate a layout from YAML on-the-fly (no file saved; layout is ephemeral)."""
    user = req.user_id
    yaml_src = req.yaml_source
    yaml_content = ""
    source_name = ""
    if "\n" not in yaml_src and yaml_src.strip().endswith(('.yaml', '.yml')):
        yaml_dir = _workflow_yaml_dir(user, req.team)
        fpath = os.path.join(yaml_dir, yaml_src.strip())
        if not os.path.isfile(fpath):
            raise HTTPException(404, f"YAML 文件不存在: {yaml_src}")
        with open(fpath, "r", encoding="utf-8") as f:
            yaml_content = f.read()
        source_name = yaml_src.replace('.yaml','').replace('.yml','')
    else:
        yaml_content = yaml_src
        source_name = "converted"

    if _yaml_to_layout_data is None:
        raise HTTPException(500, "layout 功能不可用（缺少实现）")

    try:
        layout = _yaml_to_layout_data(yaml_content)
    except Exception as e:
        raise HTTPException(400, f"YAML 转换失败: {e}")

    layout_name = req.layout_name or source_name
    layout["name"] = layout_name
    return {"status": "ok", "layout": layout_name, "data": layout}


class UserExpertRequest(BaseModel):
    user_id: str
    name: str = ""
    tag: str = ""
    persona: str = ""
    temperature: float = 0.7
    team: str = ""  # Team name for scoped expert storage


@app.post("/experts/user")
async def add_user_expert_route(req: UserExpertRequest):
    from oasis.experts import add_user_expert, add_team_expert
    try:
        if req.team:
            expert = add_team_expert(req.user_id, req.team, req.model_dump())
        else:
            expert = add_user_expert(req.user_id, req.model_dump())
        return {"status": "ok", "expert": expert}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/experts/user/{tag}")
async def update_user_expert_route(tag: str, req: UserExpertRequest):
    from oasis.experts import update_user_expert, update_team_expert
    try:
        if req.team:
            expert = update_team_expert(req.user_id, req.team, tag, req.model_dump())
        else:
            expert = update_user_expert(req.user_id, tag, req.model_dump())
        return {"status": "ok", "expert": expert}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/experts/user/{tag}")
async def delete_user_expert_route(tag: str, user_id: str = Query(...), team: str = Query("")):
    from oasis.experts import delete_user_expert, delete_team_expert
    try:
        if team:
            deleted = delete_team_expert(user_id, team, tag)
        else:
            deleted = delete_user_expert(user_id, tag)
        return {"status": "ok", "deleted": deleted}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))



# ------------------------------------------------------------------
# OpenClaw 路由（从 openclaw_routes.py 引入）
# ------------------------------------------------------------------

_OPENCLAW_BIN = _resolve_openclaw_bin()

from oasis.openclaw_routes import init_openclaw_routes
app.include_router(init_openclaw_routes(
    openclaw_bin=_OPENCLAW_BIN,
    get_env_fn=_get_env,
    skills_cache=_openclaw_skills_cache,
    managed_skills_dir=_openclaw_managed_skills_dir,
    bundled_skills=_openclaw_bundled_skills,
))

# --- System Info ---

_TUNNEL_PIDFILE = os.path.join(_project_root, ".tunnel.pid")
_IS_WINDOWS = platform.system().lower() == "windows"


def _tunnel_running() -> tuple[bool, int | None]:
    """Check if the cloudflare tunnel process is alive.
    Cleans up stale PID file if the process is dead."""
    if not os.path.isfile(_TUNNEL_PIDFILE):
        return False, None
    try:
        with open(_TUNNEL_PIDFILE) as f:
            pid = int(f.read().strip())
        if _IS_WINDOWS:
            import subprocess as _sp
            result = _sp.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            if str(pid) not in result.stdout:
                raise OSError("Process not found")
        else:
            os.kill(pid, 0)
        return True, pid
    except (ValueError, OSError):
        # PID file exists but process is dead — clean up
        try:
            os.remove(_TUNNEL_PIDFILE)
        except OSError:
            pass
        return False, None


@app.get("/publicnet/info")
async def publicnet_info():
    """Return public network info: tunnel status, public domain, ports, etc.

    This is the canonical way for agents / bots to discover the public URL
    without needing direct access to .env files.
    """
    running, pid = _tunnel_running()
    domain = ""
    if running:
        domain = _get_env("PUBLIC_DOMAIN", "")
        if domain == "wait to set":
            domain = ""

    frontend_port = _get_env("PORT_FRONTEND", "51209")
    oasis_port = _get_env("PORT_OASIS", "51202")

    return {
        "tunnel": {
            "running": running,
            "pid": pid,
            "public_domain": domain,
        },
        "ports": {
            "frontend": frontend_port,
            "oasis": oasis_port,
        },
    }



if __name__ == "__main__":
    port = int(os.getenv("PORT_OASIS", "51202"))
    uvicorn.run(app, host=_server_host(), port=port)
