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
        """Only the caller's registered ACP agents, using local acpx records."""
        from agents.store import ACPX, canonical_platform, get_store
        from external.session import runtime_session
        from external.acpx import get_acpx_adapter
        from ops.components import binary_path

        owned = {(canonical_platform(agent.platform), runtime_session(agent)): agent
                 for agent in get_store().list(user_id) if agent.driver == ACPX}
        if not owned or not binary_path("acpx"):
            return {"status": "success", "acpx_sessions": []}
        adapter = get_acpx_adapter()
        rows = []
        for platform in sorted({key[0] for key in owned}):
            for row in await adapter.list_sessions(tool=platform):
                agent = owned.get((platform, row["name"]))
                if agent is None:
                    continue
                rows.append({"platform": platform, "session_id": row.get("acpxRecordId"),
                             "name": row["name"], "agent_id": agent.agent_id,
                             "agent_name": agent.name, "cwd": row.get("cwd"),
                             "last_used_at": row.get("lastUsedAt"), "closed": row.get("closed", False)})
        return {"status": "success", "acpx_sessions": rows}


    async def close_acp_session(self, platform: str, session_name: str, cwd: str = "", *, user_id: str) -> dict:
        """Close an acpx session via 'acpx --cwd <session_cwd> <platform> sessions close <name>'.

        ``acpx`` binds every session to the cwd it was created in, and
        ``sessions close`` only acts on the current cwd (``acpx --help``:
        "Close session for current cwd"). The list rows carry each session's
        own cwd (column 3) — close must reuse *that* exact cwd, not a fixed
        store path. Closing from any other cwd prints
        ``No named session "<name>" for cwd <dir>`` and still exits 0, so the
        old fixed-``WORKSPACE_DIR/acpx`` path silently no-oped for every
        session created elsewhere while the UI reported success.
        """
        from agents.store import ACPX, canonical_platform, get_store
        from external.session import runtime_session
        from external.acpx import get_acpx_adapter
        from ops.components import binary_path
        owned = next((agent for agent in get_store().list(user_id)
                      if agent.driver == ACPX and canonical_platform(agent.platform) == canonical_platform(platform)
                      and runtime_session(agent) == session_name), None)
        if owned is None:
            raise HTTPException(404, "No owned ACP session")
        # The runtime owns cwd; never let a browser select another user's record.
        adapter = get_acpx_adapter()
        cwd = adapter._cwd
        platform = canonical_platform(owned.platform)
        acpx_bin = binary_path("acpx")
        if not acpx_bin:
            return {"status": "error", "reason": "acpx not found"}
        acpx_cwd = (cwd or "").strip() or None
        if acpx_cwd is None:
            # No session cwd supplied: fall back to the canonical store so newly
            # created sessions (which use WORKSPACE_DIR/acpx) still close.
            try:
                from common.runtime_paths import WORKSPACE_DIR  # local import to avoid cycles
                acpx_cwd = os.path.join(str(WORKSPACE_DIR), "acpx")
                os.makedirs(acpx_cwd, exist_ok=True)
            except Exception:
                acpx_cwd = None
        cmd = [acpx_bin]
        if acpx_cwd:
            cmd.extend(["--cwd", acpx_cwd])
        cmd.extend([platform, "sessions", "close", session_name])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=acpx_cwd or None,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            err_text = (stderr.decode("utf-8", errors="replace") or "").strip()
            # acpx exits 0 even when it finds nothing to close ("No named session
            # ... for cwd ..."), so returncode alone can't confirm success. Treat
            # that message as a real failure instead of a false "closed".
            if "No named session" in err_text:
                return {"status": "error", "reason": err_text[:200]}
            # exit 0 = just closed, exit 1 = already closed (both fine for idempotency)
            if proc.returncode in (0, 1):
                return {"status": "success", "stderr": err_text} if err_text else {"status": "success"}
            return {"status": "error", "reason": err_text[:200] or f"exit={proc.returncode}"}
        except Exception as e:
            return {"status": "error", "reason": str(e)}
