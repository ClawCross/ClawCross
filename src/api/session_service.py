from typing import Any, Callable

from fastapi import HTTPException

from utils.checkpoint_repository import (
    delete_thread_records,
    delete_thread_records_like,
    list_thread_ids_by_prefix,
)
from utils.logging_utils import get_logger
from api.session_models import (
    DeleteSessionRequest,
    SessionHistoryRequest,
    SessionListRequest,
    SessionStatusRequest,
)
from utils.context_compressor import estimate_messages_tokens
from utils.context_limits import resolve_history_token_budget
from utils.session_summary import build_session_summary
from webot.compression import static_compression_view
from webot.profiles import is_subagent_session
from webot.subagents import delete_subagent_by_session, delete_subagents_for_user

logger = get_logger("session_service")


class SessionService:
    """会话管理服务，提供会话列表、状态、历史、删除等功能。"""

    def __init__(
        self,
        *,
        db_path: str,
        agent: Any,
        verify_auth_or_token: Callable[[str, str, str | None], None],
        extract_text: Callable[[Any], str],
    ):
        self.db_path = db_path
        self.agent = agent
        self.verify_auth_or_token = verify_auth_or_token
        self.extract_text = extract_text

    async def _close_thread_checkpoints(self, thread_ids: list[str]) -> None:
        close_checkpoint = getattr(self.agent, "close_thread_checkpoint", None)
        if not callable(close_checkpoint):
            return
        for thread_id in thread_ids:
            await close_checkpoint(thread_id)

    async def list_sessions(self, req: SessionListRequest, x_internal_token: str | None):
        """获取用户的所有会话列表。

        :param req: 会话列表请求
        :param x_internal_token: 内部令牌（可选）
        :return: 会话列表及状态
        """
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        logger.info("list_sessions user=%s", req.user_id)

        prefix = f"{req.user_id}#"
        sessions = []

        rows = await list_thread_ids_by_prefix(self.db_path, prefix)

        for thread_id in rows:
            session_id = thread_id[len(prefix):]
            if is_subagent_session(session_id):
                continue

            config = {"configurable": {"thread_id": thread_id}}
            snapshot = await self.agent.agent_app.aget_state(config)
            msgs = snapshot.values.get("messages", []) if snapshot and snapshot.values else []

            summary = build_session_summary(
                msgs,
                skip_prefixes=("[系统触发]", "[外部学术会议邀请]"),
                title_len=50,
                last_len=50,
                list_fallback="(图片消息)",
            )
            first_human = summary["first_human"]
            last_human = summary["last_human"]
            msg_count = summary["msg_count"]

            if not first_human:
                continue

            sessions.append({
                "session_id": session_id,
                "title": first_human,
                "last_message": last_human,
                "message_count": msg_count,
            })

        return {"status": "success", "sessions": sessions}

    async def sessions_status(self, req: SessionListRequest, x_internal_token: str | None):
        """批量获取用户所有会话的运行状态。

        :param req: 会话列表请求
        :param x_internal_token: 内部令牌（可选）
        :return: 各会话的 busy/pending 状态列表
        """
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)

        prefix = f"{req.user_id}#"
        all_status = self.agent.get_all_thread_status(prefix)

        result = []
        for thread_id, info in all_status.items():
            session_id = thread_id[len(prefix):]
            if is_subagent_session(session_id):
                continue
            result.append({
                "session_id": session_id,
                "busy": info["busy"],
                "source": info["source"],       # "user" | "system" | ""
                "pending_system": info["pending_system"],
            })

        return {"status": "success", "sessions": result}

    async def get_session_history(self, req: SessionHistoryRequest, x_internal_token: str | None):
        """获取指定会话的消息历史。

        :param req: 会话历史请求
        :param x_internal_token: 内部令牌（可选）
        :return: 消息历史列表
        """
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)

        thread_id = f"{req.user_id}#{req.session_id}"
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.agent.agent_app.aget_state(config)

        if not snapshot or not snapshot.values:
            return {
                "status": "success",
                "messages": [],
                "context_percent": 0,
                "context_remaining": 0,
                "context_tokens": 0,
                "context_budget": 0,
            }

        msgs = snapshot.values.get("messages", [])

        # 静态计算：数的是 apply_compression 真正看到的输入视图
        # = [已存的 summary] + messages[compacted_until:]，
        # 不是用户在前端看到的完整未压缩历史。
        try:
            compression_view = static_compression_view(
                user_id=req.user_id,
                session_id=req.session_id,
                messages=msgs,
                checkpoint_store_path=getattr(self.agent, "_db_path", None),
            )
            static_tokens = estimate_messages_tokens(compression_view)
            # 优先用该 thread 上次推理实际使用的模型；没有就回退到 LLM_MODEL env。
            last_model = ""
            if hasattr(self.agent, "get_thread_model"):
                last_model = self.agent.get_thread_model(thread_id)
            static_budget = resolve_history_token_budget(
                is_subagent=is_subagent_session(req.session_id),
                model=last_model or None,
            )
            self.agent.set_thread_context_usage(thread_id, static_tokens, static_budget)
        except Exception:
            logger.exception("static context usage estimation failed for %s", thread_id)
        context_usage = self.agent.get_thread_context_usage(thread_id)
        result = []
        for msg in msgs:
            msg_type = type(msg).__name__
            if msg_type == "HumanMessage":
                result.append({"role": "user", "content": msg.content})
            elif msg_type == "AIMessage":
                content = self.extract_text(msg.content)
                tool_calls = []
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_calls.append({
                            "name": tc.get("name", ""),
                            "args": tc.get("args", {}),
                        })
                if content or tool_calls:
                    entry = {"role": "assistant", "content": content}
                    if tool_calls:
                        entry["tool_calls"] = tool_calls
                    result.append(entry)
            elif msg_type == "ToolMessage":
                content = self.extract_text(msg.content)
                tool_name = getattr(msg, "name", "")
                result.append({
                    "role": "tool",
                    "content": content,
                    "tool_name": tool_name,
                })

        return {
            "status": "success",
            "messages": result,
            "context_percent": int(context_usage.get("percent", 0) or 0),
            "context_remaining": int(context_usage.get("remaining", 0) or 0),
            "context_tokens": int(context_usage.get("tokens", 0) or 0),
            "context_budget": int(context_usage.get("budget", 0) or 0),
        }

    async def delete_session(self, req: DeleteSessionRequest, x_internal_token: str | None):
        """删除指定会话或用户的所有会话。

        :param req: 删除会话请求
        :param x_internal_token: 内部令牌（可选）
        :return: 删除操作结果
        """
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        logger.info("delete_session user=%s session=%s", req.user_id, req.session_id or "ALL")

        try:
            if req.session_id:
                task_key = f"{req.user_id}#{req.session_id}"
                await self.agent.cancel_task(task_key)

                thread_id = f"{req.user_id}#{req.session_id}"
                await self._close_thread_checkpoints([thread_id])
                await delete_thread_records(self.db_path, thread_id)
                if is_subagent_session(req.session_id):
                    delete_subagent_by_session(req.user_id, req.session_id)
                return {"status": "success", "message": f"会话 {req.session_id} 已删除"}

            prefix = f"{req.user_id}#"
            keys_to_cancel = self.agent.list_active_task_keys(prefix)
            for k in keys_to_cancel:
                await self.agent.cancel_task(k)

            thread_ids = await list_thread_ids_by_prefix(self.db_path, prefix)
            await self._close_thread_checkpoints(thread_ids)
            pattern = f"{req.user_id}#%"
            await delete_thread_records_like(self.db_path, pattern)
            delete_subagents_for_user(req.user_id)
            return {"status": "success", "message": f"用户 {req.user_id} 的所有会话已删除"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"删除失败: {e}")

    async def session_status(self, req: SessionStatusRequest, x_internal_token: str | None):
        """查询指定会话的实时状态。

        :param req: 会话状态请求
        :param x_internal_token: 内部令牌（可选）
        :return: 会话的 pending/busy 状态
        """
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        thread_id = f"{req.user_id}#{req.session_id}"
        has_new = self.agent.has_pending_system_messages(thread_id)
        busy = self.agent.is_thread_busy(thread_id)
        pending_count = (
            self.agent.consume_pending_system_messages(thread_id)
            if has_new and not req.peek
            else 0
        )
        busy_source = self.agent.get_thread_busy_source(thread_id) if busy else ""
        context_usage = self.agent.get_thread_context_usage(thread_id)
        return {
            "has_new_messages": has_new,
            "pending_count": pending_count,
            "busy": busy,
            "busy_source": busy_source,
            "context_percent": int(context_usage.get("percent", 0) or 0),
            "context_remaining": int(context_usage.get("remaining", 0) or 0),
            "context_tokens": int(context_usage.get("tokens", 0) or 0),
            "context_budget": int(context_usage.get("budget", 0) or 0),
        }
