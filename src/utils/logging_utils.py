"""
日志工具模块

提供统一的日志配置和 request_id 上下文传播。
所有 service 模块通过 get_logger() 获取 logger，日志自动附带 request_id。
"""
import contextvars
import logging
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOGS_DIR = os.path.join(_PROJECT_ROOT, "logs")
_ERROR_LOG_PATH = os.path.join(_LOGS_DIR, "error.log")

# --- Request ID 上下文变量 ---
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_LOG_INITIALIZED = False


class _RequestIdFilter(logging.Filter):
    """在日志 record 中注入当前 request_id。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get("-")  # type: ignore[attr-defined]
        return True


def setup_basic_logging() -> None:
    global _LOG_INITIALIZED
    if _LOG_INITIALIZED:
        return
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_format = "%(asctime)s %(levelname)s [%(name)s] [req:%(request_id)s] %(message)s"
    logging.basicConfig(
        level=level,
        format=log_format,
    )
    request_filter = _RequestIdFilter()
    # 为 root logger 添加 request_id filter
    for handler in logging.root.handlers:
        handler.addFilter(request_filter)

    # 额外输出一份独立错误日志，便于集中查看异常
    os.makedirs(_LOGS_DIR, exist_ok=True)
    error_handler = logging.FileHandler(_ERROR_LOG_PATH, encoding="utf-8")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter(log_format))
    error_handler.addFilter(request_filter)
    logging.getLogger().addHandler(error_handler)
    _LOG_INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    setup_basic_logging()
    return logging.getLogger(name)
