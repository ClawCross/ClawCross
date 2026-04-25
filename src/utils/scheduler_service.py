"""
定时任务调度服务模块

提供基于 cron 表达式的定时任务管理：
- 添加/删除/列出定时任务
- 持久化任务到 JSON 文件
- 调度时间到达时向 Agent 发送 HTTP 触发请求
"""

import os
import sys
import uuid
import json
import re
import shutil
from typing import List, Optional
from datetime import datetime
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn
from dotenv import load_dotenv

# --- 路径配置 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
src_dir = os.path.dirname(current_dir)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from api.external_agent_registry import build_external_agents_map_for_owner
from integrations.acpx_adapter import acpx_options_from_agent, load_external_agent_system_prompt
from integrations.acpx_cli_tools import acpx_agent_tags_with_legacy
from integrations.agent_sender import SendToAgentRequest, send_to_agent
from integrations.external_persona import build_external_persona_prompt

TASKS_FILE = os.path.join(root_dir, "data", "timeset", "tasks.json")

# 加载 .env 配置
load_dotenv(dotenv_path=os.path.join(root_dir, "config", ".env"))


def _server_host() -> str:
    """获取调度器绑定地址。默认为 localhost；设置 CLAWCROSS_SERVER_HOST=0.0.0.0 可暴露到所有接口。"""
    explicit_host = os.getenv("CLAWCROSS_SERVER_HOST", "").strip()
    if explicit_host:
        return explicit_host
    return "127.0.0.1"

# 确保目录存在
os.makedirs(os.path.dirname(TASKS_FILE), exist_ok=True)

# --- JSON 持久化 ---
def load_tasks() -> dict:
    """从 JSON 文件加载任务配置。"""
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_tasks(tasks: dict):
    """保存任务配置到 JSON 文件。"""
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=4)

# --- 数据模型 ---
class CronTask(BaseModel):
    """Cron 定时任务模型"""
    user_id: str
    cron: str = ""  # 格式: "分 时 日 月 周"
    text: str
    session_id: str = "default"
    target_type: str = "internal"  # internal | external
    target_ref: str = ""           # external global_name
    target_name: str = ""          # stable team display name
    team: str = ""
    schedule_type: str = "cron"    # cron | once
    run_at: str = ""               # once: ISO/local datetime, e.g. 2026-04-25T09:00

class TaskResponse(BaseModel):
    """任务响应模型"""
    task_id: str
    user_id: str
    cron: str
    text: str
    session_id: str = "default"
    target_type: str = "internal"
    target_ref: str = ""
    target_name: str = ""
    team: str = ""
    schedule_type: str = "cron"
    run_at: str = ""
    next_run: Optional[str]

# --- 全局调度器 ---
# misfire_grace_time: 错过触发后，在该秒数内仍会补触发（None=永远补触发）
# coalesce: 多次错过合并为一次执行
scheduler = AsyncIOScheduler(job_defaults={
    "misfire_grace_time": 3600,  # 错过1小时内仍补触发
    "coalesce": True,
})
PORT_AGENT = int(os.getenv("PORT_AGENT", "51200"))
AGENT_URL = f"http://127.0.0.1:{PORT_AGENT}/system_trigger"
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")
TINYFISH_MONITOR_JOB_ID = "__tinyfish_monitor__"
_ACP_TOOL_NAMES: frozenset[str] = acpx_agent_tags_with_legacy()
_AGENT_MODEL_RE = re.compile(r"^agent:[^:]+(?::(.+))?$")
_DEFAULT_ACP_SESSION_SUFFIX = "clawcrosschat"


def _parse_cron(cron_expr: str) -> list[str]:
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError("Cron must have 5 fields: minute hour day month day_of_week")
    return parts


def _schedule_type(info: dict) -> str:
    value = str(info.get("schedule_type") or "cron").strip().lower()
    return "once" if value in {"once", "at", "date"} else "cron"


def _parse_run_at(run_at: str) -> datetime:
    value = str(run_at or "").strip()
    if not value:
        raise ValueError("run_at is required for one-time alarm")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError("run_at must be ISO datetime, e.g. 2026-04-25T09:00") from e


def _target_type(info: dict) -> str:
    value = str(info.get("target_type") or "internal").strip().lower()
    return value if value in {"internal", "external"} else "internal"


def _resolve_external_session_suffix(model: str) -> str:
    m = _AGENT_MODEL_RE.match((model or "").strip())
    if m and m.group(1):
        return m.group(1)
    return _DEFAULT_ACP_SESSION_SUFFIX


def _normalize_chat_url(api_url: str) -> str:
    url = (api_url or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/chat/completions"):
        return url
    if not url.endswith("/v1"):
        url += "/v1"
    return f"{url}/chat/completions"


def _external_platform(agent_info: dict) -> str:
    platform = str(agent_info.get("platform") or agent_info.get("tag") or "").strip().lower()
    if platform in ("claude-code", "claudecode"):
        return "claude"
    if platform in ("gemini-cli", "geminicli"):
        return "gemini"
    return platform


def _find_external_agent(user_id: str, target_ref: str, team: str = "") -> dict | None:
    target_ref = str(target_ref or "").strip()
    team = str(team or "").strip()
    if not target_ref:
        return None
    candidates = build_external_agents_map_for_owner(user_id)
    agent = candidates.get(target_ref)
    if not agent:
        return None
    if team == "__public__":
        return agent if not str(agent.get("team") or "").strip() else None
    if team and str(agent.get("team") or "") not in {"", team}:
        return None
    return agent


def _find_external_agent_by_name(user_id: str, target_name: str, team: str = "") -> dict | None:
    target_name = str(target_name or "").strip()
    team = str(team or "").strip()
    if not target_name:
        return None
    matches = []
    for agent in build_external_agents_map_for_owner(user_id).values():
        agent_team = str(agent.get("team") or "").strip()
        if team == "__public__":
            if agent_team:
                continue
        elif team and agent_team != team:
            continue
        names = {
            str(agent.get("name") or "").strip(),
            str(agent.get("short_name") or "").strip(),
        }
        if target_name in names:
            matches.append(agent)
    return matches[0] if len(matches) == 1 else None


def _external_system_prompt(agent_info: dict) -> str:
    parts = [
        load_external_agent_system_prompt(root_dir),
        build_external_persona_prompt(
            str(agent_info.get("tag", "") or ""),
            user_id=str(agent_info.get("owner_user_id", "") or ""),
            team=str(agent_info.get("team", "") or ""),
        ),
    ]
    return "\n\n".join(part for part in parts if part).strip()


async def trigger_external_agent(info: dict):
    user_id = str(info.get("user_id") or "")
    target_ref = str(info.get("target_ref") or "").strip()
    target_name = str(info.get("target_name") or "").strip()
    team = str(info.get("team") or "").strip()
    agent_info = _find_external_agent_by_name(user_id, target_name, team) or _find_external_agent(user_id, target_ref, team)
    if not agent_info:
        print(f"[{datetime.now()}] 外部闹钟触发失败: user={user_id}, target={target_name or target_ref}, team={team}, 未找到外部 agent")
        return

    platform = _external_platform(agent_info)
    global_name = str(agent_info.get("global_name") or agent_info.get("global_id") or target_ref).strip()
    suffix = _resolve_external_session_suffix(str(agent_info.get("model") or ""))
    session_key = f"agent:{global_name}:{suffix}"
    schedule_label = info.get("run_at") if _schedule_type(info) == "once" else info.get("cron")
    text = (
        "[ClawCross 内部闹钟触发]\n"
        f"team: {team or '-'}\n"
        f"agent: {agent_info.get('name') or agent_info.get('short_name') or global_name}\n"
        f"schedule: {info.get('schedule_type') or 'cron'}:{schedule_label or '-'}\n\n"
        f"{info.get('text') or ''}"
    )

    api_url = str(agent_info.get("api_url") or "").strip()
    api_key = str(agent_info.get("api_key") or "").strip()
    if platform == "openclaw":
        api_url = os.getenv("OPENCLAW_API_URL", "") or api_url
        api_key = os.getenv("OPENCLAW_GATEWAY_TOKEN", "") or api_key

    if platform == "openclaw" and api_url:
        model = str(agent_info.get("model") or "").strip()
        if not model.startswith("agent:"):
            model = f"agent:{global_name}"
        headers = {"x-openclaw-session-key": session_key}
        result = await send_to_agent(SendToAgentRequest(
            prompt=text,
            connect_type="http",
            platform=platform,
            session=session_key,
            options={
                "api_url": _normalize_chat_url(api_url),
                "api_key": api_key,
                "headers": headers,
                "body": {
                    "model": model,
                    "messages": [{"role": "user", "content": text}],
                    "stream": False,
                },
                "timeout": 60,
            },
        ))
        if result.ok:
            print(f"[{datetime.now()}] 外部闹钟触发：user={user_id}, target={global_name}, backend=http")
            return
        print(f"[{datetime.now()}] 外部闹钟 HTTP 触发失败，尝试 ACP: {result.error}")

    if platform in _ACP_TOOL_NAMES and shutil.which("acpx"):
        result = await send_to_agent(SendToAgentRequest(
            prompt=text,
            connect_type="acp",
            platform=platform,
            session=session_key,
            options={
                "cwd": root_dir,
                **acpx_options_from_agent(agent_info, default_timeout_sec=180),
                "system_prompt": _external_system_prompt(agent_info),
            },
        ))
        if result.ok:
            print(f"[{datetime.now()}] 外部闹钟触发：user={user_id}, target={global_name}, backend=acp")
            return
        print(f"[{datetime.now()}] 外部闹钟 ACP 触发失败: {result.error}")
        return

    if api_url:
        result = await send_to_agent(SendToAgentRequest(
            prompt=text,
            connect_type="http",
            platform=platform,
            session=session_key,
            options={
                "api_url": _normalize_chat_url(api_url),
                "api_key": api_key,
                "model": agent_info.get("model") or "gpt-3.5-turbo",
                "system_prompt": _external_system_prompt(agent_info),
                "timeout": 60,
            },
        ))
        if result.ok:
            print(f"[{datetime.now()}] 外部闹钟触发：user={user_id}, target={global_name}, backend=http")
        else:
            print(f"[{datetime.now()}] 外部闹钟 HTTP 触发失败: {result.error}")
        return

    print(f"[{datetime.now()}] 外部闹钟触发失败: target={global_name}, platform={platform}, 无可用传输")


async def trigger_agent(user_id: str, text: str, session_id: str = "default"):
    """到达定时时间，向 Agent 发送 HTTP 请求。"""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(AGENT_URL, json={
                "user_id": user_id,
                "text": text,
                "session_id": session_id,
            }, headers={"X-Internal-Token": INTERNAL_TOKEN}, timeout=10.0)
            print(f"[{datetime.now()}] 任务触发：用户={user_id}, session={session_id}, 状态码={resp.status_code}")
        except Exception as e:
            print(f"[{datetime.now()}] 任务触发失败: {e}")


async def trigger_alarm(info: dict):
    if _target_type(info) == "external":
        await trigger_external_agent(info)
        return
    await trigger_agent(
        str(info.get("user_id") or ""),
        str(info.get("text") or ""),
        str(info.get("session_id") or "default"),
    )


async def trigger_once_alarm(task_id: str, info: dict):
    await trigger_alarm(info)
    tasks = load_tasks()
    if task_id in tasks:
        tasks.pop(task_id, None)
        save_tasks(tasks)


def _add_alarm_job(task_id: str, info: dict):
    if _schedule_type(info) == "once":
        scheduler.add_job(
            trigger_once_alarm,
            'date',
            run_date=_parse_run_at(str(info.get("run_at") or "")),
            args=[task_id, info],
            id=task_id,
            replace_existing=True,
        )
        return

    c = _parse_cron(str(info.get("cron") or ""))
    scheduler.add_job(
        trigger_alarm,
        'cron',
        minute=c[0], hour=c[1], day=c[2], month=c[3], day_of_week=c[4],
        args=[info],
        id=task_id,
        replace_existing=True
    )


def restore_tasks():
    """从 JSON 文件恢复所有定时任务到调度器。"""
    tasks = load_tasks()
    if not tasks:
        print("📭 无已保存的定时任务")
        return

    restored = 0
    for task_id, info in tasks.items():
        try:
            _add_alarm_job(task_id, info)
            restored += 1
            schedule_label = info.get("run_at") if _schedule_type(info) == "once" else info.get("cron")
            print(f"   - [ID: {task_id}] 用户: {info['user_id']}, {info.get('schedule_type', 'cron')}: {schedule_label}, session: {info.get('session_id', 'default')}, 内容: {info['text']}")
        except Exception as e:
            print(f"   ⚠️ 恢复任务 {task_id} 失败: {e}")

    print(f"✅ 已从 {TASKS_FILE} 恢复 {restored} 个定时任务")


def trigger_tinyfish_monitor():
    """到达定时时间，执行 TinyFish 竞品价格监控。"""
    try:
        from services.tinyfish_monitor_service import run_scheduled_monitor_job

        result = run_scheduled_monitor_job()
        submitted = result.get("submitted", 0)
        completed = len(result.get("results", []))
        print(f"[{datetime.now()}] TinyFish monitor 完成: submitted={submitted}, completed={completed}")
    except Exception as e:
        print(f"[{datetime.now()}] TinyFish monitor 执行失败: {e}")


def restore_tinyfish_monitor_task():
    enabled = os.getenv("TINYFISH_MONITOR_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    cron_expr = os.getenv("TINYFISH_MONITOR_CRON", "").strip()

    if not enabled:
        print("📭 TinyFish monitor 未启用")
        return
    if not cron_expr:
        print("📭 TinyFish monitor 未配置 cron")
        return

    try:
        c = _parse_cron(cron_expr)
        scheduler.add_job(
            trigger_tinyfish_monitor,
            'cron',
            minute=c[0], hour=c[1], day=c[2], month=c[3], day_of_week=c[4],
            id=TINYFISH_MONITOR_JOB_ID,
            replace_existing=True,
        )
        print(f"✅ 已恢复 TinyFish monitor 任务: cron={cron_expr}")
    except Exception as e:
        print(f"⚠️ TinyFish monitor 任务恢复失败: {e}")

# --- 生命周期 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("定时调度中心启动...")
    scheduler.start()
    restore_tasks()
    restore_tinyfish_monitor_task()
    yield
    print("定时调度中心关闭...")
    scheduler.shutdown()

app = FastAPI(title="WeBot Scheduler", lifespan=lifespan)

@app.post("/tasks", response_model=TaskResponse)
async def add_task(task: CronTask):
    task_id = str(uuid.uuid4())[:8]
    try:
        task_data = task.model_dump()
        schedule_type = _schedule_type(task_data)
        if schedule_type == "once":
            _parse_run_at(task.run_at)
        else:
            _parse_cron(task.cron)
        target_type = _target_type(task_data)
        if target_type == "external" and not (task.target_name and task.team) and not task.target_ref:
            raise ValueError("External alarm requires target_name and team")
        info = {
            "user_id": task.user_id,
            "cron": task.cron,
            "text": task.text,
            "session_id": task.session_id,
            "target_type": target_type,
            "target_ref": task.target_ref,
            "target_name": task.target_name,
            "team": task.team,
            "schedule_type": schedule_type,
            "run_at": task.run_at,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _add_alarm_job(task_id, info)
        # 持久化到 JSON
        tasks = load_tasks()
        tasks[task_id] = info
        save_tasks(tasks)

        return {**info, "task_id": task_id, "next_run": "已激活"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"定时规则错误: {e}")

@app.get("/tasks")
async def list_tasks():
    tasks = load_tasks()
    result = []
    for task_id, info in tasks.items():
        if not isinstance(info, dict):
            continue
        job = scheduler.get_job(task_id)
        result.append({
            "task_id": task_id,
            "user_id": info.get("user_id", ""),
            "text": info.get("text", ""),
            "cron": info.get("cron", ""),
            "session_id": info.get("session_id", "default"),
            "target_type": _target_type(info),
            "target_ref": info.get("target_ref", ""),
            "target_name": info.get("target_name", ""),
            "team": info.get("team", ""),
            "schedule_type": _schedule_type(info),
            "run_at": info.get("run_at", ""),
            "next_run": str(job.next_run_time) if job else None,
        })
    return result

@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    if scheduler.get_job(task_id):
        scheduler.remove_job(task_id)
        # 从 JSON 中删除
        tasks = load_tasks()
        tasks.pop(task_id, None)
        save_tasks(tasks)
        return {"status": "deleted"}
    tasks = load_tasks()
    if task_id in tasks:
        tasks.pop(task_id, None)
        save_tasks(tasks)
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="未找到任务")

if __name__ == "__main__":
    uvicorn.run(app, host=_server_host(), port=int(os.getenv("PORT_SCHEDULER", "51201")))
