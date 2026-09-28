"""
定时任务调度服务模块

提供基于 cron 表达式的定时任务管理：
- 添加/删除/列出定时任务
- 持久化任务到 JSON 文件
- 调度时间到达时把任务内容投递给目标 agent（任何 agent，经 agent 网关）
"""

import os
import sys
import uuid
import json
from typing import Optional
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

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

from agents.gateway import get_gateway
from agents.messages import AgentMessage
from agents.store import get_store, valid_agent_id
from teams.store import get_team_store
from utils.runtime_paths import DATA_DIR, ENV_FILE

TASKS_FILE = os.path.join(str(DATA_DIR), "timeset", "tasks.json")

# 加载 .env 配置
load_dotenv(dotenv_path=str(ENV_FILE))

# 本机服务互调不能走桌面代理：no_proxy 里常见的 "127.*" 写法 HTTP 客户端并不匹配，
# loopback 请求会被送进代理并拿到 502。详见 utils/local_no_proxy.py。
from utils.local_no_proxy import ensure_localhost_no_proxy

ensure_localhost_no_proxy()



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
    """Cron 定时任务模型：到点把 text 投递给 agent。"""
    user_id: str
    cron: str = ""  # 格式: "分 时 日 月 周"
    text: str
    agent: str                     # 目标 agent：编号（新编号即新 agent）或 <team>.<名字>
    team: str = ""                 # 所属 team（仅用于归类展示）
    schedule_type: str = "cron"    # cron | once
    run_at: str = ""               # once: ISO/local datetime, e.g. 2026-04-25T09:00

class TaskResponse(BaseModel):
    """任务响应模型"""
    task_id: str
    user_id: str
    cron: str
    text: str
    agent: str
    agent_name: str = ""
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
TINYFISH_MONITOR_JOB_ID = "__tinyfish_monitor__"
DASHBOARD_SUPABASE_SYNC_JOB_ID = "__dashboard_supabase_sync__"


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


async def trigger_alarm(task_id: str):
    """Deliver the task's text to its agent; the task is read now, so edits apply."""
    info = load_tasks().get(task_id)
    if not isinstance(info, dict):
        return
    agent = get_store().get(str(info.get("user_id") or ""), str(info.get("agent") or ""))
    if agent is None:
        print(f"[{datetime.now()}] 定时任务 {task_id} 的目标 agent 已不存在，跳过")
        return
    schedule = info.get("run_at") if _schedule_type(info) == "once" else info.get("cron")
    text = f"[ClawCross 定时任务 {task_id} · {info.get('schedule_type') or 'cron'}:{schedule}]\n{info.get('text') or ''}"
    receipt = await get_gateway().deliver(agent, AgentMessage(text=text, sender="scheduler"))
    status = "已投递" if receipt.accepted else f"投递失败: {receipt.error}"
    print(f"[{datetime.now()}] 定时任务 {task_id} → {agent.agent_id}: {status}")


async def trigger_once_alarm(task_id: str):
    await trigger_alarm(task_id)
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
            args=[task_id],
            id=task_id,
            replace_existing=True,
        )
        return

    c = _parse_cron(str(info.get("cron") or ""))
    scheduler.add_job(
        trigger_alarm,
        'cron',
        minute=c[0], hour=c[1], day=c[2], month=c[3], day_of_week=c[4],
        args=[task_id],
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
            print(f"   - [ID: {task_id}] 用户: {info['user_id']}, {info.get('schedule_type', 'cron')}: {schedule_label}, agent: {info.get('agent', '')}, 内容: {info['text']}")
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


def _env_enabled(key: str, default: bool = False) -> bool:
    value = os.getenv(key, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def trigger_dashboard_supabase_sync():
    """到达定时时间，将 dashboard/state/*.json 同步到 Supabase。"""
    dashboard_root = os.getenv("DASHBOARD_SUPABASE_SYNC_ROOT", "").strip()
    project_id = os.getenv("DASHBOARD_SUPABASE_SYNC_PROJECT_ID", "").strip()
    timeout_sec = int(os.getenv("DASHBOARD_SUPABASE_SYNC_TIMEOUT_SEC", "180") or "180")
    try:
        from harness.dashboard_sync import sync_dashboard_to_supabase

        result = sync_dashboard_to_supabase(
            dashboard_root=None if not dashboard_root else Path(dashboard_root).expanduser(),
            project_id=project_id,
            timeout_sec=timeout_sec,
        )
        if result.get("ok"):
            print(f"[{datetime.now()}] Dashboard Supabase sync 完成: {result.get('output') or result.get('reason')}")
        else:
            print(f"[{datetime.now()}] Dashboard Supabase sync 失败: {result.get('reason')} {result.get('error') or result.get('output') or ''}")
    except Exception as e:
        print(f"[{datetime.now()}] Dashboard Supabase sync 执行失败: {e}")


def restore_dashboard_supabase_sync_task():
    enabled = _env_enabled("DASHBOARD_SUPABASE_SYNC_ENABLED", False)
    cron_expr = os.getenv("DASHBOARD_SUPABASE_SYNC_CRON", "* * * * *").strip()

    if not enabled:
        print("📭 Dashboard Supabase sync 未启用")
        return
    if not cron_expr:
        print("📭 Dashboard Supabase sync 未配置 cron")
        return

    try:
        c = _parse_cron(cron_expr)
        scheduler.add_job(
            trigger_dashboard_supabase_sync,
            'cron',
            minute=c[0], hour=c[1], day=c[2], month=c[3], day_of_week=c[4],
            id=DASHBOARD_SUPABASE_SYNC_JOB_ID,
            replace_existing=True,
        )
        print(f"✅ 已恢复 Dashboard Supabase sync 任务: cron={cron_expr}")
    except Exception as e:
        print(f"⚠️ Dashboard Supabase sync 任务恢复失败: {e}")

# --- 生命周期 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("定时调度中心启动...")
    scheduler.start()
    restore_tasks()
    restore_tinyfish_monitor_task()
    restore_dashboard_supabase_sync_task()
    yield
    print("定时调度中心关闭...")
    scheduler.shutdown()

app = FastAPI(title="WeBot Scheduler", lifespan=lifespan)

def _task_card(task_id: str, info: dict) -> dict:
    job = scheduler.get_job(task_id)
    agent = get_store().get(str(info.get("user_id") or ""), str(info.get("agent") or ""))
    return {
        "task_id": task_id,
        "user_id": info.get("user_id", ""),
        "text": info.get("text", ""),
        "cron": info.get("cron", ""),
        "agent": info.get("agent", ""),
        "agent_name": agent.name if agent else "",
        "team": info.get("team", ""),
        "schedule_type": _schedule_type(info),
        "run_at": info.get("run_at", ""),
        "next_run": str(job.next_run_time) if job else None,
    }


@app.post("/tasks", response_model=TaskResponse)
async def add_task(task: CronTask):
    task_id = str(uuid.uuid4())[:8]
    # The target: an agent id (a new number is a new agent) or <team>.<name>.
    store = get_store()
    agent = store.get(task.user_id, task.agent) or get_team_store(store).address(task.user_id, task.agent)
    if agent is None:
        if not valid_agent_id(task.agent):
            raise HTTPException(status_code=404, detail=f"no agent {task.agent!r}")
        agent = store.ensure(task.user_id, task.agent)
    try:
        schedule_type = _schedule_type(task.model_dump())
        if schedule_type == "once":
            _parse_run_at(task.run_at)
        else:
            _parse_cron(task.cron)
        info = {
            "user_id": task.user_id,
            "cron": task.cron,
            "text": task.text,
            "agent": agent.agent_id,
            "team": task.team,
            "schedule_type": schedule_type,
            "run_at": task.run_at,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        tasks = load_tasks()
        tasks[task_id] = info
        save_tasks(tasks)
        _add_alarm_job(task_id, info)
        return {**_task_card(task_id, info), "next_run": "已激活"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"定时规则错误: {e}")

@app.get("/tasks")
async def list_tasks():
    return [_task_card(task_id, info) for task_id, info in load_tasks().items() if isinstance(info, dict)]

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
