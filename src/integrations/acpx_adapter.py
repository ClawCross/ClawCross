import asyncio
import contextlib
import json
import os
import shlex
import shutil
import tempfile
from typing import Any, Literal


class AcpxError(RuntimeError):
    pass


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
    ) -> bool:
        existed_before = await self._session_exists(tool=tool, acpx_session=acpx_session)
        await self._run_json(
            self._command_prefix(tool=tool, session_key=session_key) + ["sessions", "ensure", "--name", acpx_session],
            timeout_sec=20,
            allow_nonzero=False,
        )
        created = existed_before is False
        if created and system_prompt and system_prompt.strip():
            self._pending_initial_prompt[self._pending_prompt_key(tool=tool, acpx_session=acpx_session)] = system_prompt.strip()
        return created

    async def close_session(self, *, tool: str, session_key: str, acpx_session: str) -> None:
        # close is best-effort; missing session should not fail callers
        await self._run_json(
            self._command_prefix(tool=tool, session_key=session_key) + ["sessions", "close", acpx_session],
            timeout_sec=10,
            allow_nonzero=True,
        )

    # ── OpsService /acp_control only (do not use from group_chat prompt path) ──

    async def ops_openclaw_exec_slash(
        self,
        *,
        session_key: str,
        slash: Literal["/new", "/stop"],
        timeout_sec: int = 180,
    ) -> None:
        """``acpx … --agent 'openclaw acp --session <key>' exec '/new'|'/stop'``; no acpx ``-s``."""
        raw = f"openclaw acp --session {shlex.quote(session_key)}"
        await self._ops_run_acpx(
            ["--agent", raw, "exec", slash],
            timeout_sec=timeout_sec,
            allow_nonzero=True,
        )

    async def ops_non_openclaw_reset_session(self, *, tool: str, session_key: str) -> None:
        """``sessions close`` only; let the next real prompt recreate the session."""
        acpx_session = self.to_acpx_session_name(tool=tool, session_key=session_key)
        prefix = self._command_prefix(tool=tool, session_key=session_key)
        await self._ops_run_acpx(
            prefix + ["sessions", "close", acpx_session],
            timeout_sec=15,
            allow_nonzero=True,
        )

    async def ops_non_openclaw_cancel(self, *, tool: str, session_key: str) -> None:
        """``<tool> cancel -s <name>``."""
        acpx_session = self.to_acpx_session_name(tool=tool, session_key=session_key)
        prefix = self._command_prefix(tool=tool, session_key=session_key)
        await self._ops_run_acpx(
            prefix + ["cancel", "-s", acpx_session],
            timeout_sec=25,
            allow_nonzero=True,
        )

    async def _ops_run_acpx(self, args: list[str], *, timeout_sec: int, allow_nonzero: bool) -> str:
        """Ops-only: ``--format quiet`` (control output is not JSON-RPC)."""
        assert self._acpx_bin is not None
        approve_all = (os.getenv("ACPX_APPROVE_ALL", "1") or "").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        nip = (os.getenv("ACPX_NON_INTERACTIVE_PERMISSIONS", "") or "").strip()
        cmd: list[str] = [
            self._acpx_bin,
            "--cwd",
            self._cwd,
            "--ttl",
            "86400",
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
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError as e:
            with contextlib.suppress(Exception):
                proc.kill()
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
                }
            )
        return out

    async def prompt(
        self,
        *,
        tool: str,
        session_key: str,
        prompt_text: str,
        timeout_sec: int = 180,
        reset_session: bool = False,
        system_prompt: str | None = None,
        attachments: list[dict] | None = None,
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
            await self.close_session(tool=tool, session_key=session_key, acpx_session=acpx_session)

        # Ensure transport session on every call
        await self.ensure_session(
            tool=tool,
            session_key=session_key,
            acpx_session=acpx_session,
            system_prompt=system_prompt,
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
        )

        text = self._extract_text(output)
        if text is None:
            return output.strip()
        return text

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
    ) -> str:
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
        
        prompt_args = self._command_prefix(tool=tool, session_key=session_key)
        prompt_args += ["prompt", "-s", acpx_session, "--file", temp_path]
        try:
            return await self._run_json(
                prompt_args,
                timeout_sec=timeout_sec,
                allow_nonzero=False,
            )
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass

    async def _run_json(self, args: list[str], *, timeout_sec: int, allow_nonzero: bool) -> str:
        assert self._acpx_bin is not None
        # Headless subprocess: no TTY for permission prompts — default --approve-all so tool/exec turns can finish.
        # Opt out: ACPX_APPROVE_ALL=0|false|no|off. Optional: ACPX_NON_INTERACTIVE_PERMISSIONS=<policy>.
        approve_all = (os.getenv("ACPX_APPROVE_ALL", "1") or "").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        nip = (os.getenv("ACPX_NON_INTERACTIVE_PERMISSIONS", "") or "").strip()
        cmd: list[str] = [
            self._acpx_bin,
            "--cwd",
            self._cwd,
            "--ttl",
            "86400",
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
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError as e:
            with contextlib.suppress(Exception):
                proc.kill()
            raise AcpxError(f"acpx timeout after {timeout_sec}s: {' '.join(shlex.quote(x) for x in cmd)}") from e

        out = out_b.decode("utf-8", errors="replace")
        err = err_b.decode("utf-8", errors="replace")
        if proc.returncode != 0 and not allow_nonzero:
            msg = err.strip() or out.strip() or f"exit={proc.returncode}"
            raise AcpxError(f"acpx failed ({proc.returncode}): {msg}")
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
