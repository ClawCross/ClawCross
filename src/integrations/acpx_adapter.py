import asyncio
import contextlib
import json
import logging
import os
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Literal

from utils.oasis_acp_log import mark as _acp_mark


class AcpxError(RuntimeError):
    pass


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AcpxPromptTrace:
    text: str
    message_chunks: list[str]
    messages: list[dict[str, Any]]
    tool_uses: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    raw_output: str


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _coerce_int(value: Any, default: int, *, min_value: int, max_value: int) -> int:
    try:
        iv = int(value)
    except (TypeError, ValueError):
        iv = default
    return max(min_value, min(max_value, iv))


def _coerce_optional_int(
    value: Any,
    default: int | None,
    *,
    min_value: int,
    max_value: int,
) -> int | None:
    if value in (None, ""):
        return default
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, iv))


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


def normalize_acpx_run_options(
    options: dict[str, Any] | None = None,
    *,
    default_timeout_sec: int | None = None,
    default_ttl_sec: int = 300,
) -> dict[str, Any]:
    """Normalize acpx run policy from user/agent config.

    Defaults preserve the previous environment-driven behavior. Agent-level
    settings may override:
      - timeout_sec / acp_timeout_sec
      - ttl_sec
      - approve_all
      - non_interactive_permissions
    """
    options = options or {}
    timeout_raw = options.get("timeout_sec", options.get("acp_timeout_sec"))
    ttl_raw = options.get("ttl_sec")
    approve_all = _coerce_bool(options.get("approve_all"))
    if approve_all is None:
        approve_all = _env_bool("ACPX_APPROVE_ALL", True)
    nip = str(
        options.get(
            "non_interactive_permissions",
            os.getenv("ACPX_NON_INTERACTIVE_PERMISSIONS", ""),
        )
        or ""
    ).strip()
    return {
        "timeout_sec": _coerce_optional_int(timeout_raw, default_timeout_sec, min_value=5, max_value=3600),
        "ttl_sec": _coerce_int(ttl_raw, default_ttl_sec, min_value=60, max_value=604800),
        "approve_all": approve_all,
        "non_interactive_permissions": nip,
    }


def acpx_options_from_agent(
    agent_info: dict[str, Any] | None,
    *,
    overrides: dict[str, Any] | None = None,
    default_timeout_sec: int | None = None,
) -> dict[str, Any]:
    """Resolve ACPX policy from an external agent record and optional request overrides."""
    agent_info = agent_info or {}
    meta = agent_info.get("meta") if isinstance(agent_info.get("meta"), dict) else {}
    acp = {}
    if isinstance(meta, dict):
        acp = meta.get("acp") or meta.get("acpx") or {}
        if not isinstance(acp, dict):
            acp = {}
    merged: dict[str, Any] = {}
    for src in (agent_info, meta, acp, overrides or {}):
        if not isinstance(src, dict):
            continue
        for key in ("timeout_sec", "acp_timeout_sec", "ttl_sec", "approve_all", "non_interactive_permissions"):
            if key in src and src[key] not in (None, ""):
                merged[key] = src[key]
    return normalize_acpx_run_options(merged, default_timeout_sec=default_timeout_sec)


class AcpxAdapter:
    """Minimal async wrapper around acpx CLI sessions/prompt."""

    def __init__(self, *, cwd: str | None = None):
        self._acpx_bin = shutil.which("acpx")
        self._cwd = cwd or os.getcwd()
        self._pending_initial_prompt: dict[str, str] = {}
        if not self._acpx_bin:
            raise AcpxError("acpx binary not found in PATH")

    @property
    def available(self) -> bool:
        return bool(self._acpx_bin)

    @staticmethod
    def to_acpx_session_name(*, tool: str, session_key: str) -> str:
        # Use business session key directly as acpx transport session.
        return session_key

    async def ensure_session(
        self,
        *,
        tool: str,
        session_key: str,
        acpx_session: str,
        system_prompt: str | None = None,
        ttl_sec: int = 300,
        approve_all: bool | None = None,
        non_interactive_permissions: str | None = None,
    ) -> bool:
        existed_before = await self._session_exists(tool=tool, acpx_session=acpx_session)
        await self._run_json(
            self._command_prefix(tool=tool, session_key=session_key) + ["sessions", "ensure", "--name", acpx_session],
            timeout_sec=20,
            allow_nonzero=False,
            ttl_sec=ttl_sec,
            approve_all=approve_all,
            non_interactive_permissions=non_interactive_permissions,
        )
        created = existed_before is False
        if created and system_prompt and system_prompt.strip():
            self._pending_initial_prompt[self._pending_prompt_key(tool=tool, acpx_session=acpx_session)] = system_prompt.strip()
        return created

    async def close_session(
        self,
        *,
        tool: str,
        session_key: str,
        acpx_session: str,
        timeout_sec: int = 10,
        ttl_sec: int = 300,
        approve_all: bool | None = None,
        non_interactive_permissions: str | None = None,
    ) -> None:
        # Safety first: cancel any in-flight prompt before closing the transport
        # session record. Missing/idle sessions should not fail callers.
        try:
            await self.cancel_session(
                tool=tool,
                session_key=session_key,
                acpx_session=acpx_session,
                timeout_sec=timeout_sec,
                ttl_sec=ttl_sec,
                approve_all=approve_all,
                non_interactive_permissions=non_interactive_permissions,
            )
        except AcpxError as e:
            logger.warning("acpx cancel before close failed for %s: %s", acpx_session, e)
        await self._run_json(
            self._command_prefix(tool=tool, session_key=session_key) + ["sessions", "close", acpx_session],
            timeout_sec=timeout_sec,
            allow_nonzero=True,
            ttl_sec=ttl_sec,
            approve_all=approve_all,
            non_interactive_permissions=non_interactive_permissions,
        )

    async def cancel_session(
        self,
        *,
        tool: str,
        session_key: str,
        acpx_session: str,
        timeout_sec: int = 10,
        ttl_sec: int = 300,
        approve_all: bool | None = None,
        non_interactive_permissions: str | None = None,
    ) -> None:
        await self._run_json(
            self._command_prefix(tool=tool, session_key=session_key) + ["cancel", "-s", acpx_session],
            timeout_sec=timeout_sec,
            allow_nonzero=True,
            ttl_sec=ttl_sec,
            approve_all=approve_all,
            non_interactive_permissions=non_interactive_permissions,
        )

    # ── OpsService /acp_control only (do not use from group_chat prompt path) ──

    async def ops_openclaw_exec_slash(
        self,
        *,
        session_key: str,
        slash: Literal["/new", "/stop"],
        timeout_sec: int = 180,
        ttl_sec: int = 300,
        approve_all: bool | None = None,
        non_interactive_permissions: str | None = None,
    ) -> None:
        """``acpx … --agent 'openclaw acp --session <key>' exec '/new'|'/stop'``; no acpx ``-s``."""
        raw = f"openclaw acp --session {shlex.quote(session_key)}"
        await self._ops_run_acpx(
            ["--agent", raw, "exec", slash],
            timeout_sec=timeout_sec,
            allow_nonzero=True,
            ttl_sec=ttl_sec,
            approve_all=approve_all,
            non_interactive_permissions=non_interactive_permissions,
        )

    async def ops_non_openclaw_reset_session(
        self,
        *,
        tool: str,
        session_key: str,
        timeout_sec: int = 15,
        ttl_sec: int = 300,
        approve_all: bool | None = None,
        non_interactive_permissions: str | None = None,
    ) -> None:
        """``sessions close`` only; let the next real prompt recreate the session."""
        acpx_session = self.to_acpx_session_name(tool=tool, session_key=session_key)
        prefix = self._command_prefix(tool=tool, session_key=session_key)
        await self._ops_run_acpx(
            prefix + ["sessions", "close", acpx_session],
            timeout_sec=timeout_sec,
            allow_nonzero=True,
            ttl_sec=ttl_sec,
            approve_all=approve_all,
            non_interactive_permissions=non_interactive_permissions,
        )

    async def ops_non_openclaw_cancel(
        self,
        *,
        tool: str,
        session_key: str,
        timeout_sec: int = 25,
        ttl_sec: int = 300,
        approve_all: bool | None = None,
        non_interactive_permissions: str | None = None,
    ) -> None:
        """``<tool> cancel -s <name>``."""
        acpx_session = self.to_acpx_session_name(tool=tool, session_key=session_key)
        prefix = self._command_prefix(tool=tool, session_key=session_key)
        await self._ops_run_acpx(
            prefix + ["cancel", "-s", acpx_session],
            timeout_sec=timeout_sec,
            allow_nonzero=True,
            ttl_sec=ttl_sec,
            approve_all=approve_all,
            non_interactive_permissions=non_interactive_permissions,
        )

    async def _ops_run_acpx(
        self,
        args: list[str],
        *,
        timeout_sec: int,
        allow_nonzero: bool,
        ttl_sec: int = 300,
        approve_all: bool | None = None,
        non_interactive_permissions: str | None = None,
    ) -> str:
        """Ops-only: ``--format quiet`` (control output is not JSON-RPC)."""
        assert self._acpx_bin is not None
        if approve_all is None:
            approve_all = _env_bool("ACPX_APPROVE_ALL", True)
        nip = (non_interactive_permissions or os.getenv("ACPX_NON_INTERACTIVE_PERMISSIONS", "") or "").strip()
        cmd: list[str] = [
            self._acpx_bin,
            "--cwd",
            self._cwd,
            "--ttl",
            str(ttl_sec),
        ]
        if approve_all:
            cmd.append("--approve-all")
        if nip:
            cmd.extend(["--non-interactive-permissions", nip])
        cmd.extend(["--format", "quiet", *args])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise AcpxError(f"acpx executable missing: {e}") from e
        try:
            if timeout_sec is None:
                out_b, err_b = await proc.communicate()
            else:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError as e:
            with contextlib.suppress(Exception):
                proc.kill()
                await proc.wait()
            raise AcpxError(
                f"acpx timeout after {timeout_sec}s: {' '.join(shlex.quote(x) for x in cmd)}"
            ) from e
        out = out_b.decode("utf-8", errors="replace")
        err = err_b.decode("utf-8", errors="replace")
        rc = proc.returncode if proc.returncode is not None else -1
        if rc != 0 and not allow_nonzero:
            msg = err.strip() or out.strip() or f"exit={rc}"
            raise AcpxError(f"acpx failed ({rc}): {msg}")
        return out

    async def list_sessions(self, *, tool: str) -> list[dict[str, Any]]:
        """Run `acpx <tool> sessions list --format json` and return slim session rows."""
        aliases = {
            "claude-code": "claude",
            "gemini-cli": "gemini",
        }
        tool_n = aliases.get((tool or "").strip().lower(), (tool or "").strip().lower())
        if tool_n == "openclaw":
            raise AcpxError("sessions list is not supported for openclaw agent mode")
        raw = await self._run_json([tool_n, "sessions", "list"], timeout_sec=45, allow_nonzero=False)
        text = raw.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # NDJSON or trailing noise: take first JSON array line
            rows: list[Any] = []
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("["):
                    try:
                        rows = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            data = rows
        if isinstance(data, dict) and "sessions" in data:
            items = data["sessions"]
        elif isinstance(data, list):
            items = data
        else:
            items = []
        out: list[dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = it.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            out.append(
                {
                    "name": name.strip(),
                    "acpxRecordId": it.get("acpxRecordId"),
                    "closed": bool(it.get("closed")),
                    "lastUsedAt": it.get("lastUsedAt"),
                    "cwd": it.get("cwd"),
                    "title": it.get("title"),
                    "message_count": len(it.get("messages") or []) if isinstance(it.get("messages"), list) else 0,
                }
            )
        return out

    async def show_session(self, *, tool: str, name: str) -> dict[str, Any]:
        """Run `acpx <tool> sessions show <name>` and return parsed metadata."""
        raw = await self._run_json(
            self._command_prefix(tool=tool, session_key=name) + ["sessions", "show", name],
            timeout_sec=20,
            allow_nonzero=False,
        )
        text = raw.strip()
        if not text:
            return {}
        # `sessions show` is plain text, not JSON.
        result: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip()
        return result

    async def session_history(self, *, tool: str, name: str, limit: int = 20) -> dict[str, Any]:
        """Run `acpx <tool> sessions history <name>` and return parsed JSON."""
        raw = await self._run_json(
            self._command_prefix(tool=tool, session_key=name) + ["sessions", "history", name, "--limit", str(limit)],
            timeout_sec=30,
            allow_nonzero=False,
        )
        try:
            data = json.loads(raw.strip() or "{}")
        except json.JSONDecodeError as e:
            raise AcpxError(f"invalid acpx sessions history JSON: {e}") from e
        return data if isinstance(data, dict) else {}

    async def read_session(self, *, tool: str, name: str, tail: int | None = None) -> dict[str, Any]:
        """Run `acpx <tool> sessions read <name>` and return parsed JSON."""
        args = self._command_prefix(tool=tool, session_key=name) + ["sessions", "read", name]
        if isinstance(tail, int) and tail > 0:
            args.extend(["--tail", str(tail)])
        raw = await self._run_json(
            args,
            timeout_sec=45,
            allow_nonzero=False,
        )
        try:
            data = json.loads(raw.strip() or "{}")
        except json.JSONDecodeError as e:
            raise AcpxError(f"invalid acpx sessions read JSON: {e}") from e
        return data if isinstance(data, dict) else {}

    async def prompt(
        self,
        *,
        tool: str,
        session_key: str,
        prompt_text: str,
        timeout_sec: int | None = None,
        reset_session: bool = False,
        system_prompt: str | None = None,
        attachments: list[dict] | None = None,
        ttl_sec: int = 300,
        approve_all: bool | None = None,
        non_interactive_permissions: str | None = None,
    ) -> str:
        """
        Send a prompt to the agent.
        
        Args:
            tool: Agent tool name (openclaw, claude, codex, etc.)
            session_key: Session key
            prompt_text: Text prompt
            timeout_sec: Timeout in seconds
            reset_session: Whether to reset the session
            attachments: List of attachments with keys:
                - type: "image" | "audio" | "text"
                - mime_type: MIME type string
                - data: base64-encoded content
                - name: filename
        """
        acpx_session = self.to_acpx_session_name(tool=tool, session_key=session_key)
        if reset_session:
            await self.close_session(
                tool=tool,
                session_key=session_key,
                acpx_session=acpx_session,
                ttl_sec=ttl_sec,
                approve_all=approve_all,
                non_interactive_permissions=non_interactive_permissions,
            )

        # Ensure transport session on every call
        await self.ensure_session(
            tool=tool,
            session_key=session_key,
            acpx_session=acpx_session,
            system_prompt=system_prompt,
            ttl_sec=ttl_sec,
            approve_all=approve_all,
            non_interactive_permissions=non_interactive_permissions,
        )

        pending_prompt = self._pending_initial_prompt.pop(
            self._pending_prompt_key(tool=tool, acpx_session=acpx_session),
            "",
        )
        effective_prompt = prompt_text
        if pending_prompt:
            effective_prompt = f"{pending_prompt}\n\n{prompt_text}".strip()

        output = await self._send_prompt_file(
            tool=tool,
            session_key=session_key,
            acpx_session=acpx_session,
            prompt_text=effective_prompt,
            timeout_sec=timeout_sec,
            attachments=attachments,
            ttl_sec=ttl_sec,
            approve_all=approve_all,
            non_interactive_permissions=non_interactive_permissions,
        )

        text = self._extract_text(output)
        if text is None:
            return output.strip()
        return text

    async def prompt_with_trace(
        self,
        *,
        tool: str,
        session_key: str,
        prompt_text: str,
        timeout_sec: int | None = None,
        reset_session: bool = False,
        system_prompt: str | None = None,
        attachments: list[dict] | None = None,
        ttl_sec: int = 300,
        approve_all: bool | None = None,
        non_interactive_permissions: str | None = None,
    ) -> AcpxPromptTrace:
        acpx_session = self.to_acpx_session_name(tool=tool, session_key=session_key)
        if reset_session:
            await self.close_session(
                tool=tool,
                session_key=session_key,
                acpx_session=acpx_session,
                ttl_sec=ttl_sec,
                approve_all=approve_all,
                non_interactive_permissions=non_interactive_permissions,
            )

        await self.ensure_session(
            tool=tool,
            session_key=session_key,
            acpx_session=acpx_session,
            system_prompt=system_prompt,
            ttl_sec=ttl_sec,
            approve_all=approve_all,
            non_interactive_permissions=non_interactive_permissions,
        )

        pending_prompt = self._pending_initial_prompt.pop(
            self._pending_prompt_key(tool=tool, acpx_session=acpx_session),
            "",
        )
        effective_prompt = prompt_text
        if pending_prompt:
            effective_prompt = f"{pending_prompt}\n\n{prompt_text}".strip()

        output = await self._send_prompt_file(
            tool=tool,
            session_key=session_key,
            acpx_session=acpx_session,
            prompt_text=effective_prompt,
            timeout_sec=timeout_sec,
            attachments=attachments,
            ttl_sec=ttl_sec,
            approve_all=approve_all,
            non_interactive_permissions=non_interactive_permissions,
        )
        return self._extract_trace(output)

    async def _session_exists(self, *, tool: str, acpx_session: str) -> bool | None:
        normalized_tool = (tool or "").strip().lower()
        if normalized_tool == "openclaw":
            return None
        try:
            sessions = await self.list_sessions(tool=normalized_tool)
        except Exception:
            return None
        return any(
            str(row.get("name") or "").strip() == acpx_session
            and not bool(row.get("closed"))
            for row in sessions
        )

    async def _send_prompt_file(
        self,
        *,
        tool: str,
        session_key: str,
        acpx_session: str,
        prompt_text: str,
        timeout_sec: int,
        attachments: list[dict] | None,
        ttl_sec: int,
        approve_all: bool | None,
        non_interactive_permissions: str | None,
    ) -> str:
        prompt_args, temp_path = self.prepare_prompt_command(
            tool=tool,
            session_key=session_key,
            acpx_session=acpx_session,
            prompt_text=prompt_text,
            attachments=attachments,
            ttl_sec=ttl_sec,
            approve_all=approve_all,
            non_interactive_permissions=non_interactive_permissions,
        )
        try:
            return await self._run_json_command(
                prompt_args,
                timeout_sec=timeout_sec,
                allow_nonzero=False,
            )
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass

    def prepare_prompt_command(
        self,
        *,
        tool: str,
        session_key: str,
        acpx_session: str,
        prompt_text: str,
        attachments: list[dict] | None,
        ttl_sec: int,
        approve_all: bool | None,
        non_interactive_permissions: str | None,
    ) -> tuple[list[str], str]:
        """Build the exact acpx prompt command plus temp JSON payload path."""
        assert self._acpx_bin is not None

        # Build multimodal prompt content (JSON array)
        content_blocks = [{"type": "text", "text": prompt_text.strip() or "(empty prompt)"}]

        if attachments:
            for att in attachments:
                att_type = att.get("type", "")
                mime_type = att.get("mime_type", "")
                data = att.get("data", "")

                # 处理带 data: 前缀的 base64（如前端 "data:image/png;base64,xxx"）
                if data.startswith("data:"):
                    # 提取 mime type 和纯 base64
                    header, b64data = data.split(",", 1)
                    if ";" in header:
                        # 提取 mime type，如 "data:image/png;base64" → "image/png"
                        incoming_mime = header.replace("data:", "").split(";")[0]
                        if not mime_type:
                            mime_type = incoming_mime
                    data = b64data
                
                if att_type == "image" and data:
                    content_blocks.append({
                        "type": "image",
                        "mimeType": mime_type or "image/png",
                        "data": data,
                    })
                elif att_type == "audio" and data:
                    content_blocks.append({
                        "type": "input_audio",
                        "mimeType": mime_type or "audio/wav",
                        "data": data,
                    })
        
        # Always use --file mode for multimodal content to avoid argument length limits
        # Also use unique filename to avoid conflicts when multiple agents are running
        json_content = json.dumps(content_blocks, ensure_ascii=False)
        
        # Generate unique temp file name with timestamp to avoid conflicts
        import time
        import uuid
        unique_suffix = f"{int(time.time()*1000)}_{uuid.uuid4().hex[:8]}"
        temp_path = os.path.join(tempfile.gettempdir(), f"acpx_prompt_{unique_suffix}.json")
        
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(json_content)
        
        if approve_all is None:
            approve_all = _env_bool("ACPX_APPROVE_ALL", True)
        nip = (non_interactive_permissions or os.getenv("ACPX_NON_INTERACTIVE_PERMISSIONS", "") or "").strip()
        cmd: list[str] = [
            self._acpx_bin,
            "--cwd",
            self._cwd,
            "--ttl",
            str(ttl_sec),
        ]
        if approve_all:
            cmd.append("--approve-all")
        if nip:
            cmd.extend(["--non-interactive-permissions", nip])
        cmd.extend(["--format", "json", "--json-strict"])
        cmd.extend(self._command_prefix(tool=tool, session_key=session_key))
        cmd.extend(["prompt", "-s", acpx_session, "--file", temp_path])
        return cmd, temp_path

    async def _run_json(
        self,
        args: list[str],
        *,
        timeout_sec: int,
        allow_nonzero: bool,
        ttl_sec: int = 300,
        approve_all: bool | None = None,
        non_interactive_permissions: str | None = None,
    ) -> str:
        assert self._acpx_bin is not None
        # Headless subprocess: no TTY for permission prompts — default --approve-all so tool/exec turns can finish.
        # Opt out: ACPX_APPROVE_ALL=0|false|no|off. Optional: ACPX_NON_INTERACTIVE_PERMISSIONS=<policy>.
        if approve_all is None:
            approve_all = _env_bool("ACPX_APPROVE_ALL", True)
        nip = (non_interactive_permissions or os.getenv("ACPX_NON_INTERACTIVE_PERMISSIONS", "") or "").strip()
        cmd: list[str] = [
            self._acpx_bin,
            "--cwd",
            self._cwd,
            "--ttl",
            str(ttl_sec),
        ]
        if approve_all:
            cmd.append("--approve-all")
        if nip:
            cmd.extend(["--non-interactive-permissions", nip])
        cmd.extend(
            [
                "--format",
                "json",
                "--json-strict",
                *args,
            ]
        )
        _aux_tail = " ".join(cmd[3:7]) if len(cmd) > 7 else " ".join(cmd[3:])
        _acp_mark("acpx.aux.spawn.pre", op=_aux_tail, timeout_sec=timeout_sec)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _acp_mark("acpx.aux.spawn.post", pid=proc.pid, op=_aux_tail)
        try:
            if timeout_sec is None:
                out_b, err_b = await proc.communicate()
            else:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError as e:
            _acp_mark("acpx.aux.timeout", pid=proc.pid, op=_aux_tail)
            with contextlib.suppress(Exception):
                proc.kill()
                await proc.wait()
            raise AcpxError(f"acpx timeout after {timeout_sec}s: {' '.join(shlex.quote(x) for x in cmd)}") from e

        out = out_b.decode("utf-8", errors="replace")
        err = err_b.decode("utf-8", errors="replace")
        _acp_mark(
            "acpx.aux.done",
            pid=proc.pid,
            op=_aux_tail,
            returncode=proc.returncode,
            stdout_chars=len(out),
        )
        if proc.returncode != 0 and not allow_nonzero:
            msg = err.strip() or out.strip() or f"exit={proc.returncode}"
            raise AcpxError(f"acpx failed ({proc.returncode}): {msg}")
        return out

    async def _run_json_command(
        self,
        cmd: list[str],
        *,
        timeout_sec: int | None,
        allow_nonzero: bool,
    ) -> str:
        # Cheap fingerprint of the command for trace correlation without leaking prompts.
        _cmd_tail = " ".join(cmd[-4:]) if len(cmd) > 4 else " ".join(cmd)
        _acp_mark("acpx.spawn.pre", cmd_tail=_cmd_tail, timeout_sec=timeout_sec)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
        )
        _acp_mark("acpx.spawn.post", pid=proc.pid)
        stdout_chunks: list[bytes] = []
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + timeout_sec) if timeout_sec is not None else None

        def _remaining_timeout() -> float | None:
            if deadline is None:
                return None
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            return remaining

        try:
            if proc.stdout is not None:
                line_no = 0
                total_stdout_bytes = 0
                while True:
                    read_t0 = loop.time()
                    _acp_mark(
                        "acpx.stdout.readline.start",
                        pid=proc.pid,
                        line_no=line_no + 1,
                        returncode=proc.returncode,
                    )
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=_remaining_timeout())
                    read_elapsed = loop.time() - read_t0
                    if not line:
                        _acp_mark(
                            "acpx.stdout.eof",
                            pid=proc.pid,
                            line_no=line_no + 1,
                            read_elapsed=f"{read_elapsed:.3f}s",
                            returncode=proc.returncode,
                            stdout_bytes=total_stdout_bytes,
                        )
                        break
                    line_no += 1
                    total_stdout_bytes += len(line)
                    stdout_chunks.append(line)
                    _acp_mark(
                        "acpx.stdout.line",
                        pid=proc.pid,
                        line_no=line_no,
                        read_elapsed=f"{read_elapsed:.3f}s",
                        line_bytes=len(line),
                        stdout_bytes=total_stdout_bytes,
                        returncode=proc.returncode,
                    )
            _acp_mark("acpx.wait.start", pid=proc.pid, returncode=proc.returncode)
            wait_t0 = loop.time()
            returncode = await asyncio.wait_for(proc.wait(), timeout=_remaining_timeout())
            _acp_mark(
                "acpx.wait.done",
                pid=proc.pid,
                returncode=returncode,
                wait_elapsed=f"{loop.time() - wait_t0:.3f}s",
            )
        except asyncio.TimeoutError as e:
            _acp_mark("acpx.timeout", pid=proc.pid, timeout_sec=timeout_sec)
            with contextlib.suppress(Exception):
                proc.kill()
                await proc.wait()
            raise AcpxError(f"acpx timeout after {timeout_sec}s: {' '.join(shlex.quote(x) for x in cmd)}") from e

        out_b = b"".join(stdout_chunks)
        out = out_b.decode("utf-8", errors="replace")
        _acp_mark(
            "acpx.stream.done",
            pid=proc.pid,
            returncode=returncode,
            stdout_chars=len(out),
        )
        if returncode != 0 and not allow_nonzero:
            msg = out.strip() or f"exit={returncode}"
            raise AcpxError(f"acpx failed ({returncode}): {msg}")
        return out

    @staticmethod
    def _command_prefix(*, tool: str, session_key: str) -> list[str]:
        aliases = {
            "claude-code": "claude",
            "gemini-cli": "gemini",
        }
        tool = aliases.get((tool or "").strip().lower(), (tool or "").strip().lower())
        # openclaw must use raw --agent command in this environment.
        if tool == "openclaw":
            raw = f"openclaw acp --session {shlex.quote(session_key)}"
            return ["--agent", raw]
        return [tool]

    @staticmethod
    def _pending_prompt_key(*, tool: str, acpx_session: str) -> str:
        return f"{(tool or '').strip().lower()}\x1f{acpx_session}"

    @staticmethod
    def _extract_acpx_agent_message_chunks(obj: Any) -> str | None:
        """ACP JSON-RPC line: assistant-visible text from session/update agent_message_chunk."""
        if not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0":
            return None
        if obj.get("method") != "session/update":
            return None
        params = obj.get("params")
        if not isinstance(params, dict):
            return None
        upd = params.get("update")
        if not isinstance(upd, dict):
            return None
        if upd.get("sessionUpdate") != "agent_message_chunk":
            return None
        content = upd.get("content")
        if not isinstance(content, dict):
            return ""
        if content.get("type") != "text":
            return ""
        t = content.get("text")
        return t if isinstance(t, str) else ""

    @staticmethod
    def extract_stream_event(obj: Any) -> dict[str, Any] | None:
        """Parse realtime ACPX session/update events useful for frontend streaming."""
        if not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0":
            return None
        if obj.get("method") != "session/update":
            return None
        params = obj.get("params")
        if not isinstance(params, dict):
            return None
        upd = params.get("update")
        if not isinstance(upd, dict):
            return None

        update_type = upd.get("sessionUpdate")
        if update_type == "agent_message_chunk":
            content = upd.get("content")
            if not isinstance(content, dict):
                return None
            if content.get("type") != "text":
                return None
            text = content.get("text")
            if not isinstance(text, str):
                return None
            return {"type": "agent_message_chunk", "text": text}

        if update_type == "tool_call":
            return {
                "type": "tool_call",
                "tool_call_id": str(upd.get("toolCallId") or ""),
                "title": str(upd.get("title") or ""),
                "kind": str(upd.get("kind") or ""),
                "status": str(upd.get("status") or ""),
                "raw_input": upd.get("rawInput"),
                "locations": upd.get("locations") if isinstance(upd.get("locations"), list) else [],
            }

        if update_type == "tool_call_update":
            parts: list[str] = []
            for item in upd.get("content") or []:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "text" and isinstance(content.get("text"), str):
                    parts.append(content["text"])
            return {
                "type": "tool_call_update",
                "tool_call_id": str(upd.get("toolCallId") or ""),
                "content_text": "".join(parts),
            }

        return None

    @staticmethod
    def _extract_text(output: str) -> str | None:
        """Parse acpx stdout: JSON-RPC stream (session/update … agent_message_chunk) or legacy summary JSON."""
        message_parts: list[str] = []
        legacy: str | None = None
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            part = AcpxAdapter._extract_acpx_agent_message_chunks(obj)
            if part is not None:
                message_parts.append(part)
            cand = AcpxAdapter._pick_text(obj)
            if cand:
                legacy = cand
        assembled = "".join(message_parts).strip()
        if assembled:
            return assembled
        return legacy

    @staticmethod
    def _extract_trace(output: str) -> AcpxPromptTrace:
        message_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        tool_uses: list[dict[str, Any]] = []
        tool_results: list[dict[str, Any]] = []
        legacy: str | None = None

        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            part = AcpxAdapter._extract_acpx_agent_message_chunks(obj)
            if part is not None:
                message_parts.append(part)

            if isinstance(obj, dict):
                if obj.get("schema") == "acpx.session.v1" and isinstance(obj.get("messages"), list):
                    for item in obj.get("messages") or []:
                        if isinstance(item, dict):
                            messages.append(item)
                            for role_payload in item.values():
                                if not isinstance(role_payload, dict):
                                    continue
                                content = role_payload.get("content")
                                if isinstance(content, list):
                                    for block in content:
                                        if not isinstance(block, dict):
                                            continue
                                        if "ToolUse" in block and isinstance(block["ToolUse"], dict):
                                            tool_uses.append(block["ToolUse"])
                                results = role_payload.get("tool_results")
                                if isinstance(results, dict):
                                    for result in results.values():
                                        if isinstance(result, dict):
                                            tool_results.append(result)

            cand = AcpxAdapter._pick_text(obj)
            if cand:
                legacy = cand

        # Keep the primary assistant text extraction identical to the legacy
        # prompt() path so main-page chat rendering stays stable.
        extracted_text = AcpxAdapter._extract_text(output)
        assembled = (extracted_text or "").strip() or (legacy or output.strip())
        return AcpxPromptTrace(
            text=assembled,
            message_chunks=message_parts,
            messages=messages,
            tool_uses=tool_uses,
            tool_results=tool_results,
            raw_output=output,
        )

    @staticmethod
    def _pick_text(obj: Any) -> str | None:
        if isinstance(obj, dict):
            for key in ("text", "content", "message", "summary", "reply"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    return val
            result = obj.get("result")
            if isinstance(result, dict):
                return AcpxAdapter._pick_text(result)
            if isinstance(result, str) and result.strip():
                return result
        return None


_adapter_singletons: dict[str, AcpxAdapter] = {}


def get_acpx_adapter(*, cwd: str | None = None) -> AcpxAdapter:
    key = os.path.realpath(cwd or os.getcwd())
    adapter = _adapter_singletons.get(key)
    if adapter is None:
        adapter = AcpxAdapter(cwd=key)
        _adapter_singletons[key] = adapter
    return adapter


def load_external_agent_system_prompt(project_root: str) -> str:
    prompt_path = os.path.join(project_root, "data", "prompts", "external_agent_system.txt")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def load_external_agent_prompt_file(project_root: str, filename: str) -> str:
    prompt_path = os.path.join(project_root, "data", "prompts", filename)
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""
