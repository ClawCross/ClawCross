"""
OpenAI 兼容 API 服务模块

提供 OpenAI Chat Completions API 的实现：
- 处理聊天补全请求（流式/非流式）
- 管理 agent 工具白名单
- 支持工具调用和外部工具集成
"""

import asyncio
import json
import os
from typing import Any, Callable

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from utils.auth_utils import extract_user_password_session, is_internal_bearer, parse_bearer_parts
from utils.logging_utils import get_logger
from api.openai_models import ChatCompletionRequest, ChatMessage, OpenAIExecutionContext
from api.openai_protocol import OpenAIProtocolHelper

logger = get_logger("openai_service")
_GRAPH_RECURSION_LIMIT = 100

# --- Agent tool whitelist ---
_USER_FILES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "user_files",
)


def _iter_user_internal_agent_files(user_id: str) -> list[str]:
    """Return internal agent config files scoped to one user only."""
    scoped_user_id = (user_id or "").strip()
    if not scoped_user_id:
        return []

    user_dir = os.path.join(_USER_FILES_DIR, scoped_user_id)
    if not os.path.isdir(user_dir):
        return []

    files: list[str] = []
    user_ia = os.path.join(user_dir, "internal_agents.json")
    if os.path.isfile(user_ia):
        files.append(user_ia)

    teams_dir = os.path.join(user_dir, "teams")
    if not os.path.isdir(teams_dir):
        return files

    for team_name in sorted(os.listdir(teams_dir)):
        team_ia = os.path.join(teams_dir, team_name, "internal_agents.json")
        if os.path.isfile(team_ia):
            files.append(team_ia)
    return files


def _get_agent_tool_whitelist(user_id: str, session_id: str) -> set[str] | None:
    """根据当前 user_id + session_id 查找匹配的 internal agent tools 白名单。

    只在当前用户自己的 internal_agents.json 与其 team 目录内查找，避免跨用户
    session_id 碰撞导致白名单串用。

    如果该 agent 配置了 tools 白名单，返回允许的工具名集合。

    Returns:
      set[str] — 白名单集合（只包含值为 true 的 key）
      None     — 未找到匹配 agent 或该 agent 未配置 tools（不限制）
    """
    if not session_id or session_id == "default":
        return None

    for ia_file in _iter_user_internal_agent_files(user_id):
        try:
            with open(ia_file, "r", encoding="utf-8") as f:
                agents_list = json.load(f)
            if not isinstance(agents_list, list):
                continue
            for agent_entry in agents_list:
                if not isinstance(agent_entry, dict):
                    continue
                if agent_entry.get("session") != session_id:
                    continue
                # 找到匹配的 agent
                meta = agent_entry.get("meta")
                tools_cfg = None
                if isinstance(meta, dict):
                    tools_cfg = meta.get("tools")
                if tools_cfg is None:
                    # Backward compatibility for older flat entries.
                    tools_cfg = agent_entry.get("tools")
                if not isinstance(tools_cfg, dict) or not tools_cfg:
                    return None  # 该 agent 无 tools 配置 → 不限制
                return {k for k, v in tools_cfg.items() if v}
        except Exception:
            continue
    return None


class OpenAIChatService:
    """OpenAI 兼容聊天服务，提供 Chat Completions API 实现。"""

    def __init__(
        self,
        *,
        internal_token: str,
        verify_password: Callable[[str, str], bool],
        agent: Any,
        extract_text: Callable[[Any], str],
        build_human_message: Callable[[str, list[str] | None, list[dict] | None, list[dict] | None], HumanMessage],
    ):
        self.internal_token = internal_token
        self.verify_password = verify_password
        self.agent = agent
        self.extract_text = extract_text
        self.protocol = OpenAIProtocolHelper(build_human_message=build_human_message)

    def openai_msg_to_human_message(self, msg: ChatMessage) -> HumanMessage:
        """将 OpenAI 格式消息转换为 HumanMessage。"""
        return self.protocol.openai_msg_to_human_message(msg)

    def make_completion_id(self) -> str:
        return self.protocol.make_completion_id()

    def make_openai_response(
        self,
        content: str,
        model: str = "webot",
        finish_reason: str = "stop",
        tool_calls: list[dict] | None = None,
    ) -> dict:
        return self.protocol.make_openai_response(
            content,
            model=model,
            finish_reason=finish_reason,
            tool_calls=tool_calls,
        )

    def make_openai_chunk(
        self,
        content: str = "",
        model: str = "webot",
        finish_reason: str | None = None,
        completion_id: str = "",
        meta: dict | None = None,
    ) -> str:
        return self.protocol.make_openai_chunk(
            completion_id=completion_id,
            content=content,
            model=model,
            finish_reason=finish_reason,
            meta=meta,
        )

    def extract_external_tool_names(self, tools: list[dict] | None) -> set[str]:
        return self.protocol.extract_external_tool_names(tools)

    def format_tool_calls_for_openai(self, ai_msg: AIMessage, external_names: set[str]) -> list[dict] | None:
        return self.protocol.format_tool_calls_for_openai(ai_msg, external_names)

    @staticmethod
    def _exception_chain(exc: Exception, limit: int = 8) -> list[Exception]:
        chain: list[Exception] = []
        seen: set[int] = set()
        cur: Exception | None = exc
        while cur is not None and id(cur) not in seen and len(chain) < limit:
            chain.append(cur)
            seen.add(id(cur))
            next_exc = cur.__cause__ or cur.__context__
            cur = next_exc if isinstance(next_exc, Exception) else None
        return chain

    def _exception_chain_text(self, exc: Exception) -> str:
        parts: list[str] = []
        for item in self._exception_chain(exc):
            msg = str(item).strip() or item.__class__.__name__
            parts.append(f"{item.__class__.__name__}: {msg}")
        return " <- ".join(parts)

    @staticmethod
    def _preview_text(value: Any, limit: int = 1200) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            try:
                text = json.dumps(value, ensure_ascii=False)
            except Exception:
                text = str(value)
        else:
            text = str(value)
        text = text.strip()
        if len(text) > limit:
            return text[:limit] + "…"
        return text

    def _extract_exception_details(self, exc: Exception) -> dict[str, str]:
        details: dict[str, str] = {}
        for item in self._exception_chain(exc):
            request = getattr(item, "request", None)
            if request is not None:
                details.setdefault("request_method", getattr(request, "method", "") or "")
                with_id = str(getattr(request, "url", "") or "").strip()
                if with_id:
                    details.setdefault("request_url", with_id)

            request_id = getattr(item, "request_id", None)
            if request_id:
                details.setdefault("request_id", str(request_id))

            body = getattr(item, "body", None)
            body_preview = self._preview_text(body)
            if body_preview:
                details.setdefault("response_body", body_preview)

            response = getattr(item, "response", None)
            if response is not None:
                status_code = getattr(response, "status_code", None)
                if status_code is not None:
                    details.setdefault("status_code", str(status_code))
                headers = getattr(response, "headers", None)
                if headers is not None:
                    rid = headers.get("request-id") or headers.get("x-request-id")
                    if rid:
                        details.setdefault("request_id", rid)
                if "response_body" not in details:
                    with contextlib.suppress(Exception):
                        resp_text = (response.text or "").strip()
                        if resp_text:
                            details["response_body"] = self._preview_text(resp_text)
        return details

    def _llm_config_hint(self) -> str:
        provider = (os.getenv("LLM_PROVIDER") or "").strip()
        model = (os.getenv("LLM_MODEL") or "").strip()
        base_url = (os.getenv("LLM_BASE_URL") or "").strip()
        parts = []
        if provider:
            parts.append(f"provider={provider}")
        if model:
            parts.append(f"model={model}")
        if base_url:
            parts.append(f"base_url={base_url}")
        return "；当前默认配置: " + " ".join(parts) if parts else ""

    def _diagnose_exception(self, exc: Exception) -> str:
        chain_text = self._exception_chain_text(exc)
        lowered = chain_text.lower()

        if "illegal status line" in lowered:
            return (
                "上游模型兼容网关/代理返回了损坏的 HTTP 响应，"
                "不是提示词或工具参数错误。常见于第三方 Anthropic 兼容端点、"
                "反向代理、CDN 或中转层协议异常。"
                + self._llm_config_hint()
            )
        if "apiconnectionerror" in lowered or "remoteprotocolerror" in lowered:
            return (
                "这是上游模型连接/协议层错误，不是前端刷新逻辑问题，"
                "也不像是 prompt 或 tool 参数本身导致。更像是模型服务、代理网关或网络链路异常。"
                + self._llm_config_hint()
            )
        if "timeout" in lowered:
            return "上游模型请求超时，通常是模型服务响应过慢或网络链路阻塞。" + self._llm_config_hint()
        return "内部调用失败，需结合完整异常链继续排查。"

    def _build_user_facing_error_text(self, exc: Exception) -> str:
        chain = self._exception_chain(exc)
        top = chain[0] if chain else exc
        root = chain[-1] if chain else exc
        top_msg = str(top).strip() or top.__class__.__name__
        root_msg = str(root).strip() or root.__class__.__name__
        diagnosis = self._diagnose_exception(exc)
        details = self._extract_exception_details(exc)
        text = (
            f"响应异常: {top.__class__.__name__}: {top_msg}\n"
            f"根因: {root.__class__.__name__}: {root_msg}\n"
            f"原因分析: {diagnosis}"
        )
        response_body = details.get("response_body", "")
        if response_body:
            text += f"\n返回内容: {response_body}"
        return text

    def auth_openai_request(self, req: ChatCompletionRequest, auth_header: str | None):
        """从 OpenAI 请求中提取认证信息并验证。"""
        user_id = req.user
        password = req.password
        session_override = None

        parts = parse_bearer_parts(auth_header)
        if parts:
            if is_internal_bearer(parts, self.internal_token):
                if len(parts) >= 3:
                    return parts[1], True, parts[2]
                if len(parts) == 2:
                    return parts[1], True, None
                return user_id or "system", True, None

            parsed = extract_user_password_session(parts, default_session="")
            if parsed:
                user_id, password, session_override = parsed

        if not user_id or not password:
            return None, False, None
        if not self.verify_password(user_id, password):
            return None, False, None
        return user_id, True, session_override

    def _build_input_messages(self, req: ChatCompletionRequest):
        input_messages = []
        last_user_msg = None

        trailing_tool_msgs = []
        trailing_tool_assistant = None
        i = len(req.messages) - 1
        while i >= 0:
            msg = req.messages[i]
            if msg.role == "tool":
                trailing_tool_msgs.insert(0, msg)
                i -= 1
            elif msg.role == "assistant" and msg.tool_calls and trailing_tool_msgs:
                trailing_tool_assistant = msg
                i -= 1
                break
            else:
                break

        if trailing_tool_msgs:
            tool_result_messages: list[Any] = []
            if trailing_tool_assistant and trailing_tool_assistant.tool_calls:
                normalized_tool_calls = []
                for tc in trailing_tool_assistant.tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    function = tc.get("function") or {}
                    raw_args = function.get("arguments", {})
                    parsed_args = raw_args
                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args)
                        except Exception:
                            parsed_args = {}
                    normalized_tool_calls.append(
                        {
                            "id": tc.get("id", ""),
                            "name": function.get("name", ""),
                            "args": parsed_args if isinstance(parsed_args, dict) else {},
                        }
                    )
                tool_result_messages.append(
                    AIMessage(
                        content=self.extract_text(trailing_tool_assistant.content),
                        tool_calls=normalized_tool_calls,
                    )
                )
            for tmsg in trailing_tool_msgs:
                tool_result_messages.append(
                    ToolMessage(
                        content=tmsg.content if isinstance(tmsg.content, str) else json.dumps(tmsg.content, ensure_ascii=False),
                        tool_call_id=tmsg.tool_call_id or "",
                        name=tmsg.name or "",
                    )
                )
            return tool_result_messages

        system_parts = []
        for msg in req.messages:
            if msg.role == "system" and msg.content:
                system_parts.append(msg.content if isinstance(msg.content, str) else str(msg.content))

        for msg in reversed(req.messages):
            if msg.role == "user":
                last_user_msg = msg
                break
        if not last_user_msg:
            raise HTTPException(status_code=400, detail="messages 中缺少 user 或 tool 消息")

        human_msg = self.openai_msg_to_human_message(last_user_msg)
        if system_parts:
            sys_text = "\n".join(system_parts)
            if isinstance(human_msg.content, list):
                human_msg.content.insert(0, {"type": "text", "text": f"[来自调度方的指令]\n{sys_text}\n\n---\n"})
            else:
                human_msg.content = f"[来自调度方的指令]\n{sys_text}\n\n---\n{human_msg.content}"
        input_messages = [human_msg]
        return input_messages

    async def _patch_cancelled_tool_calls(self, config: dict) -> None:
        try:
            snapshot = await self.agent.agent_app.aget_state(config)
            last_msgs = snapshot.values.get("messages", [])
            if last_msgs:
                last_msg_item = last_msgs[-1]
                tool_messages = self.agent.cancelled_tool_messages_for_last_ai(last_msg_item)
                if tool_messages:
                    await self.agent.agent_app.aupdate_state(config, {"messages": tool_messages})
        except Exception:
            pass

    async def _run_non_stream(
        self,
        ctx: OpenAIExecutionContext,
    ):
        task_key = f"{ctx.user_id}#{ctx.session_id}"
        await self.agent.cancel_task(task_key)

        async def non_stream_worker():
            async with ctx.thread_lock:
                self.agent.set_thread_busy_source(ctx.thread_id, "user")
                try:
                    result = await self.agent.agent_app.ainvoke(ctx.user_input, ctx.config, durability="exit")
                    await self.agent.purge_checkpoints(ctx.thread_id)
                    return result
                finally:
                    self.agent.clear_thread_busy_source(ctx.thread_id)

        task = asyncio.create_task(non_stream_worker())
        self.agent.register_task(task_key, task)
        try:
            result = await task
        except asyncio.CancelledError:
            logger.info("non-stream cancelled user=%s session=%s", ctx.user_id, ctx.session_id)
            await self._patch_cancelled_tool_calls(ctx.config)
            return self.make_openai_response("⚠️ 已终止", model=ctx.model_name)
        except Exception as e:
            error_chain = self._exception_chain_text(e)
            diagnosis = self._diagnose_exception(e)
            details = self._extract_exception_details(e)
            logger.exception(
                "non-stream chat failed user=%s session=%s model=%s error=%s chain=%s diagnosis=%s request=%s %s status=%s request_id=%s response_body=%s",
                ctx.user_id,
                ctx.session_id,
                ctx.model_name,
                e,
                error_chain,
                diagnosis,
                details.get("request_method", ""),
                details.get("request_url", ""),
                details.get("status_code", ""),
                details.get("request_id", ""),
                details.get("response_body", ""),
            )
            raise HTTPException(status_code=500, detail=self._build_user_facing_error_text(e)) from e
        finally:
            self.agent.unregister_task(task_key)

        last_msg = result["messages"][-1]
        ext_tool_calls = self.format_tool_calls_for_openai(last_msg, ctx.external_tool_names)
        if ext_tool_calls:
            return self.make_openai_response(
                self.extract_text(last_msg.content), model=ctx.model_name, tool_calls=ext_tool_calls
            )

        reply = self.extract_text(last_msg.content)
        return self.make_openai_response(reply, model=ctx.model_name)

    async def _run_stream(
        self,
        ctx: OpenAIExecutionContext,
    ):
        task_key = f"{ctx.user_id}#{ctx.session_id}"
        await self.agent.cancel_task(task_key)

        queue: asyncio.Queue[str | None] = asyncio.Queue()
        completion_id = self.make_completion_id()

        async def stream_worker():
            collected_tokens = []
            chatbot_round = 0
            active_tool_names = []
            async with ctx.thread_lock:
                self.agent.set_thread_busy_source(ctx.thread_id, "user")
                try:
                    await queue.put(self.make_openai_chunk("", model=ctx.model_name, completion_id=completion_id))

                    async for event in self.agent.agent_app.astream_events(ctx.user_input, ctx.config, version="v2", durability="exit"):
                        kind = event.get("event", "")
                        ev_name = event.get("name", "")

                        if kind == "on_chain_start" and ev_name == "chatbot":
                            chatbot_round += 1
                            if chatbot_round > 1:
                                await queue.put(self.make_openai_chunk(
                                    model=ctx.model_name,
                                    completion_id=completion_id,
                                    meta={"type": "ai_start", "round": chatbot_round},
                                ))
                        elif kind == "on_chain_start" and ev_name == "tools":
                            active_tool_names = []
                            await queue.put(self.make_openai_chunk(
                                model=ctx.model_name,
                                completion_id=completion_id,
                                meta={"type": "tools_start"},
                            ))
                        elif kind == "on_chain_end" and ev_name == "tools":
                            await queue.put(self.make_openai_chunk(
                                model=ctx.model_name,
                                completion_id=completion_id,
                                meta={"type": "tools_end", "tools": active_tool_names},
                            ))
                        elif kind == "on_tool_start":
                            tool_name = ev_name
                            if tool_name not in ctx.external_tool_names:
                                active_tool_names.append(tool_name)
                                await queue.put(self.make_openai_chunk(
                                    model=ctx.model_name,
                                    completion_id=completion_id,
                                    meta={"type": "tool_start", "name": tool_name},
                                ))
                        elif kind == "on_tool_end":
                            tool_name = ev_name
                            if tool_name not in ctx.external_tool_names:
                                output = event.get("data", {}).get("output", "")
                                if hasattr(output, "content"):
                                    output = output.content
                                output_str = str(output)[:200] if output else ""
                                await queue.put(self.make_openai_chunk(
                                    model=ctx.model_name,
                                    completion_id=completion_id,
                                    meta={"type": "tool_end", "name": tool_name, "result": output_str},
                                ))
                        elif kind == "on_chat_model_stream":
                            chunk = event.get("data", {}).get("chunk")
                            if chunk and hasattr(chunk, "content") and chunk.content:
                                text = self.extract_text(chunk.content)
                                if text:
                                    collected_tokens.append(text)
                                    await queue.put(self.make_openai_chunk(
                                        text, model=ctx.model_name, completion_id=completion_id
                                    ))

                    snapshot = await self.agent.agent_app.aget_state(ctx.config)
                    last_msgs = snapshot.values.get("messages", [])
                    if last_msgs:
                        last_msg_item = last_msgs[-1]
                        ext_tool_calls = self.format_tool_calls_for_openai(last_msg_item, ctx.external_tool_names)
                        if ext_tool_calls:
                            await queue.put(self.protocol.make_tool_calls_chunk(
                                completion_id=completion_id,
                                model=ctx.model_name,
                                tool_calls=ext_tool_calls,
                            ))
                            await queue.put("data: [DONE]\n\n")
                            return

                    await queue.put(self.make_openai_chunk(
                        "", model=ctx.model_name, finish_reason="stop", completion_id=completion_id
                    ))
                    await queue.put("data: [DONE]\n\n")
                except asyncio.CancelledError:
                    await self._patch_cancelled_tool_calls(ctx.config)
                    partial_text = "".join(collected_tokens)
                    if partial_text:
                        partial_text += "\n\n⚠️ （回复被用户终止）"
                        partial_msg = AIMessage(content=partial_text)
                        await self.agent.agent_app.aupdate_state(ctx.config, {"messages": [partial_msg]})
                    await queue.put(self.make_openai_chunk(
                        "\n\n⚠️ 已终止思考", model=ctx.model_name, completion_id=completion_id
                    ))
                    await queue.put(self.make_openai_chunk(
                        "", model=ctx.model_name, finish_reason="stop", completion_id=completion_id
                    ))
                    await queue.put("data: [DONE]\n\n")
                except Exception as e:
                    error_chain = self._exception_chain_text(e)
                    diagnosis = self._diagnose_exception(e)
                    details = self._extract_exception_details(e)
                    logger.exception(
                        "stream chat failed user=%s session=%s model=%s error=%s chain=%s diagnosis=%s request=%s %s status=%s request_id=%s response_body=%s",
                        ctx.user_id,
                        ctx.session_id,
                        ctx.model_name,
                        e,
                        error_chain,
                        diagnosis,
                        details.get("request_method", ""),
                        details.get("request_url", ""),
                        details.get("status_code", ""),
                        details.get("request_id", ""),
                        details.get("response_body", ""),
                    )
                    await queue.put(self.make_openai_chunk(
                        f"\n❌ {self._build_user_facing_error_text(e)}",
                        model=ctx.model_name,
                        completion_id=completion_id,
                    ))
                    await queue.put(self.make_openai_chunk(
                        "", model=ctx.model_name, finish_reason="stop", completion_id=completion_id
                    ))
                    await queue.put("data: [DONE]\n\n")
                finally:
                    self.agent.clear_thread_busy_source(ctx.thread_id)
                    await self.agent.purge_checkpoints(ctx.thread_id)
                    await queue.put(None)
                    self.agent.unregister_task(task_key)

        task = asyncio.create_task(stream_worker())
        self.agent.register_task(task_key, task)

        async def event_generator():
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def handle_chat_completions(
        self,
        req: ChatCompletionRequest,
        authorization: str | None,
    ):
        user_id, authenticated, session_override = self.auth_openai_request(req, authorization)
        if not authenticated:
            raise HTTPException(status_code=401, detail="认证失败")

        session_id = session_override or req.session_id or "default"
        thread_id = f"{user_id}#{session_id}"
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": _GRAPH_RECURSION_LIMIT,
        }

        external_tool_names = self.extract_external_tool_names(req.tools)
        input_messages = self._build_input_messages(req)

        # --- Agent tool whitelist filtering ---
        agent_whitelist = _get_agent_tool_whitelist(user_id, session_id)
        effective_enabled = req.enabled_tools
        if agent_whitelist is not None:
            if effective_enabled is None:
                # 调用方未限制 → 直接用白名单
                effective_enabled = list(agent_whitelist)
            else:
                # 调用方有自己的限制 → 取交集
                effective_enabled = [t for t in effective_enabled if t in agent_whitelist]

        user_input = {
            "messages": input_messages,
            "trigger_source": "user",
            "enabled_tools": effective_enabled,
            "user_id": user_id,
            "session_id": session_id,
            "max_turns": req.max_turns,
            "max_tokens": req.max_tokens,
            "turn_count": 0,
            "external_tools": req.tools,
        }
        # Per-request LLM model override (from OASIS SessionExpert)
        if req.llm_override:
            user_input["llm_override"] = req.llm_override

        model_name = req.model or "webot"
        thread_lock = await self.agent.get_thread_lock(thread_id)
        ctx = OpenAIExecutionContext(
            user_id=user_id,
            session_id=session_id,
            thread_id=thread_id,
            config=config,
            user_input=user_input,
            model_name=model_name,
            external_tool_names=external_tool_names,
            thread_lock=thread_lock,
            max_tokens=req.max_tokens,
        )

        logger.info("chat user=%s session=%s stream=%s model=%s",
                    user_id, session_id, req.stream, model_name)

        if not req.stream:
            return await self._run_non_stream(ctx)

        return await self._run_stream(ctx)

    def list_models(self) -> dict:
        return self.protocol.list_models_payload()
