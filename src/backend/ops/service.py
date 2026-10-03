import asyncio
import os
import shutil
from typing import Any, Callable

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from common.auth_utils import extract_user_password_session, is_internal_bearer, parse_bearer_parts
from ops.update_manager import current_update_snapshot, start_update_process
from common.llm_factory import get_provider_audio_defaults, infer_provider
from common.logging_utils import get_logger
from ops.models import LoginRequest, TTSRequest, UpdateCheckRequest, UpdateStartRequest, UpdateStatusRequest

logger = get_logger("ops_service")


class OpsService:
    """操作服务类，提供工具列表、登录、TTS、更新与外部会话清理。"""

    def __init__(
        self,
        *,
        internal_token: str,
        agent: Any,
        verify_password: Callable[[str, str], bool],
        verify_auth_or_token: Callable[[str, str, str | None], None],
    ):
        self.internal_token = internal_token
        self.agent = agent
        self.verify_password = verify_password
        self.verify_auth_or_token = verify_auth_or_token

    async def get_tools_list(self, x_internal_token: str | None, authorization: str | None):
        """获取可用工具列表。

        :param x_internal_token: 内部令牌（可选）
        :param authorization: Bearer 授权头（可选）
        :return: 包含工具列表的字典
        :raises HTTPException: 认证失败时抛出 403 异常
        """
        if x_internal_token and x_internal_token == self.internal_token:
            return {"status": "success", "tools": self.agent.get_tools_info()}
        parts = parse_bearer_parts(authorization)
        if parts:
            if is_internal_bearer(parts, self.internal_token):
                return {"status": "success", "tools": self.agent.get_tools_info()}
            parsed = extract_user_password_session(parts, default_session="default")
            if parsed and self.verify_password(parsed[0], parsed[1]):
                return {"status": "success", "tools": self.agent.get_tools_info()}
        raise HTTPException(status_code=403, detail="认证失败")

    async def login(self, req: LoginRequest):
        """用户登录验证。

        :param req: 登录请求，包含 user_id 和 password
        :return: 登录成功状态
        :raises HTTPException: 密码错误时抛出 401 异常
        """
        if self.verify_password(req.user_id, req.password):
            logger.info("login success user=%s", req.user_id)
            return {"status": "success", "message": "登录成功"}
        logger.warning("login failed user=%s", req.user_id)
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # ------------------------------------------------------------------
    # Unified agent catalog and control plane
    # ------------------------------------------------------------------

    async def text_to_speech(self, req: TTSRequest, x_internal_token: str | None):
        """文本转语音（TTS）服务。

        :param req: TTS 请求，包含 text、voice 等
        :param x_internal_token: 内部令牌（可选）
        :return: 音频流响应
        :raises HTTPException: 未配置 API 或文本为空时抛出异常
        """
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)

        tts_text = req.text.strip()
        if not tts_text:
            raise HTTPException(status_code=400, detail="文本不能为空")
        if len(tts_text) > 4000:
            tts_text = tts_text[:4000]

        api_key = os.getenv("LLM_API_KEY", "")
        base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
        provider = infer_provider(
            model=os.getenv("LLM_MODEL", ""),
            base_url=base_url,
            provider=os.getenv("LLM_PROVIDER", ""),
            api_key=api_key,
        )
        audio_defaults = get_provider_audio_defaults(provider)
        tts_model = os.getenv("TTS_MODEL", "").strip() or audio_defaults["tts_model"]
        tts_voice = req.voice or os.getenv("TTS_VOICE", "").strip() or audio_defaults["tts_voice"]

        if not api_key or not base_url:
            raise HTTPException(status_code=500, detail="TTS API 未配置")
        if not tts_model:
            raise HTTPException(
                status_code=500,
                detail="TTS_MODEL 未配置，且当前 LLM provider 没有可自动推断的音频默认值",
            )

        tts_url = f"{base_url}/audio/speech"

        async def audio_stream():
            payload = {
                "model": tts_model,
                "input": tts_text,
                "response_format": "mp3",
            }
            if tts_voice:
                payload["voice"] = tts_voice

            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    tts_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                ) as resp:
                    if resp.status_code != 200:
                        error_body = await resp.aread()
                        raise HTTPException(
                            status_code=resp.status_code,
                            detail=f"TTS API 错误: {error_body.decode('utf-8', errors='replace')[:200]}",
                        )
                    async for chunk in resp.aiter_bytes(chunk_size=4096):
                        yield chunk

        return StreamingResponse(
            audio_stream(),
            media_type="audio/mpeg",
            headers={"Content-Disposition": "inline; filename=tts_output.mp3"},
        )

    # ------------------------------------------------------------------
    # ACP external agent management
    # ------------------------------------------------------------------

    async def update_check(self, req: UpdateCheckRequest, x_internal_token: str | None):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        try:
            snapshot = current_update_snapshot(fetch_remote=bool(req.refresh_remote))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"status": "success", "update": snapshot}

    async def update_start(self, req: UpdateStartRequest, x_internal_token: str | None):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        try:
            status = start_update_process(req.user_id, branch=req.branch)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"status": "success", "update": status}

    async def update_status(self, req: UpdateStatusRequest, x_internal_token: str | None):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        try:
            snapshot = current_update_snapshot(fetch_remote=False)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"status": "success", "update": snapshot}

    async def list_all_sessions(self, user_id: str) -> dict:
        from agents.acp_sessions import list_all_sessions
        from ops.components import binary_path
        return await list_all_sessions(user_id, binary_path("acpx"))

    async def close_acp_session(self, platform: str, session_name: str, cwd: str = "", *, user_id: str) -> dict:
        from agents.acp_sessions import close_acp_session
        from ops.components import binary_path
        return await close_acp_session(platform, session_name, cwd, user_id=user_id, acpx_bin=binary_path("acpx"))
