"""
主 Agent 服务入口模块

FastAPI 应用入口，整合所有路由和服务：
- 初始化日志和请求 ID 中间件
- 初始化数据库和用户认证
- 注册所有 API 路由（session、group、system、settings、ops、openai）
- 提供 CORS 支持
"""

import os
import secrets
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn

from dotenv import load_dotenv

# API patch（提供音频格式适配和 MIME 修复）
from utils.api_patch import patch_langchain_file_mime
patch_langchain_file_mime()

from core.agent import TeamAgent
from services.llm_factory import extract_text as _extract_text
from utils.user_auth import load_users as load_users_from_file, verify_password as verify_password_from_file
from api.group_routes import create_group_router, init_group_db
from api.harness_routes import create_harness_router
from api.openai_routes import create_openai_router
from api.ops_routes import create_ops_router
from api.session_routes import create_session_router
from api.settings_routes import create_settings_router
from api.system_routes import create_system_router
from webot.routes import create_webot_router
from services.message_builder import build_human_message
from utils.logging_utils import get_logger, request_id_ctx
from utils.checkpoint_paths import DEFAULT_CHECKPOINT_DB_DIR
from utils.runtime_paths import DATA_DIR, ENV_FILE, USERS_FILE, ensure_runtime_dirs

# --- Path setup ---
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
logger = get_logger("mainagent")
ensure_runtime_dirs()


# --- Request ID Middleware ---
class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个请求生成或传播 X-Request-Id，并注入日志上下文。"""

    async def dispatch(self, request: Request, call_next):
        req_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
        token = request_id_ctx.set(req_id)
        try:
            response: Response = await call_next(request)
            response.headers["X-Request-Id"] = req_id
            return response
        finally:
            request_id_ctx.reset(token)

env_path = str(ENV_FILE)
db_path = str(DEFAULT_CHECKPOINT_DB_DIR)
group_db_path = str(DATA_DIR / "group_chat.db")
users_path = str(USERS_FILE)

load_dotenv(dotenv_path=env_path)


# --- Internal token for service-to-service auth ---
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "").strip()
if not INTERNAL_TOKEN:
    # Auto-generate a token and append to .env (replacing any empty INTERNAL_TOKEN= line)
    INTERNAL_TOKEN = secrets.token_hex(32)
    # Read existing content, replace empty placeholder if present
    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()
    if "INTERNAL_TOKEN=" in content:
        # Replace empty or placeholder line with real value
        import re
        content = re.sub(
            r"^INTERNAL_TOKEN=\s*$",
            f"INTERNAL_TOKEN={INTERNAL_TOKEN}",
            content,
            flags=re.MULTILINE,
        )
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        with open(env_path, "a", encoding="utf-8") as f:
            f.write(f"\n# 内部服务间通信密钥（自动生成，勿泄露）\nINTERNAL_TOKEN={INTERNAL_TOKEN}\n")
    logger.info("已自动生成 INTERNAL_TOKEN 并写入 %s", env_path)


def verify_internal_token(token: str | None):
    """校验内部服务通信 token，失败抛 403"""
    if not token or token != INTERNAL_TOKEN:
        raise HTTPException(status_code=403, detail="无效的内部通信凭证")


def verify_auth_or_token(user_id: str, password: str = "",
                         x_internal_token: str | None = None):
    """Verify authentication via password OR X-Internal-Token.
    Raises HTTPException on failure.
    """
    # 1. Internal token takes priority
    if x_internal_token and x_internal_token == INTERNAL_TOKEN:
        return
    # 2. Fall back to password verification
    if password and verify_password(user_id, password):
        return
    raise HTTPException(status_code=401, detail="用户名或密码错误")


# --- User auth helpers ---
def load_users() -> dict:
    """加载用户名-密码哈希配置。"""
    return load_users_from_file(users_path)


def verify_password(username: str, password: str) -> bool:
    """验证用户密码：对输入密码做 sha256 后与配置中的哈希比对。"""
    return verify_password_from_file(users_path, username, password)


# --- Create agent instance ---
agent = TeamAgent(src_dir=current_dir, db_path=db_path)


# --- FastAPI lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent.startup()
    await init_group_db(group_db_path)   # 初始化群聊数据库（on_event 与 lifespan 不兼容）
    yield
    await agent.shutdown()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

# --- CORS: 允许前端直连 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)
# --- Request ID 传播 ---
app.add_middleware(RequestIdMiddleware)

app.include_router(
    create_group_router(
        internal_token=INTERNAL_TOKEN,
        verify_password=verify_password,
        checkpoint_db_path=db_path,
        group_db_path=group_db_path,
        agent=agent,
    )
)

app.include_router(
    create_session_router(
        db_path=db_path,
        agent=agent,
        verify_auth_or_token=verify_auth_or_token,
        extract_text=_extract_text,
    )
)


app.include_router(
    create_openai_router(
        internal_token=INTERNAL_TOKEN,
        verify_password=verify_password,
        agent=agent,
        extract_text=_extract_text,
        build_human_message=build_human_message,
    )
)


app.include_router(
    create_ops_router(
        internal_token=INTERNAL_TOKEN,
        agent=agent,
        verify_password=verify_password,
        verify_auth_or_token=verify_auth_or_token,
        group_db_path=group_db_path,
    )
)

app.include_router(
    create_settings_router(
        env_path=env_path,
        verify_auth_or_token=verify_auth_or_token,
    )
)

app.include_router(
    create_webot_router(
        agent=agent,
        verify_auth_or_token=verify_auth_or_token,
        extract_text=_extract_text,
    )
)

app.include_router(
    create_harness_router(
        verify_auth_or_token=verify_auth_or_token,
    )
)

app.include_router(
    create_system_router(
        agent=agent,
        verify_internal_token=verify_internal_token,
    )
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT_AGENT", "51200")))
