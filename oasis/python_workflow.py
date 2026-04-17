from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from typing import Any

_SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from integrations.agent_sender import SendToAgentResult
from oasis.agent_center import AgentCenter
from oasis.forum import DiscussionForum

_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


def workflow_python_dir(user_id: str, team: str = "") -> str:
    if team:
        return os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "teams", team, "oasis", "python")
    return os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "oasis", "python")


def resolve_python_workflow_path(user_id: str, python_file: str, team: str = "") -> tuple[str | None, str | None]:
    if not python_file:
        return None, "未提供 python workflow 文件名"
    target_name = python_file if python_file.endswith(".py") else f"{python_file}.py"
    matches: list[tuple[str, str]] = []
    user_root = os.path.join(_PROJECT_ROOT, "data", "user_files", user_id)
    if team:
        search_dirs = [("team", team, workflow_python_dir(user_id, team))]
    else:
        search_dirs = [("personal", "", workflow_python_dir(user_id, ""))]
        teams_root = os.path.join(user_root, "teams")
        if os.path.isdir(teams_root):
            for team_name in sorted(os.listdir(teams_root)):
                team_dir = os.path.join(teams_root, team_name)
                if os.path.isdir(team_dir):
                    search_dirs.append(("team", team_name, workflow_python_dir(user_id, team_name)))
    for scope, team_name, base_dir in search_dirs:
        path = os.path.join(base_dir, target_name)
        if os.path.isfile(path):
            label = f"team:{team_name}" if scope == "team" else "personal"
            matches.append((label, path))
    if not matches:
        return None, f"未找到 python workflow 文件: {target_name}"
    if len(matches) > 1:
        where = ", ".join(label for label, _ in matches)
        return None, f"找到多个同名 python workflow: {target_name}（{where}），请指定 team"
    return matches[0][1], None


class PythonWorkflowContext:
    def __init__(self, forum: DiscussionForum, *, user_id: str, team: str, question: str):
        self.forum = forum
        self.user_id = user_id
        self.team = team
        self.question = question
        self._agent_center = AgentCenter(user_id, team)

    def list_agents(self) -> list[dict[str, Any]]:
        return self._agent_center.list_agents()

    def list_personas(self) -> list[dict[str, Any]]:
        return self._agent_center.list_personas()

    def get_agent(self, target: str) -> dict[str, Any]:
        return self._agent_center.get_agent(target)

    def get_persona(self, target: str) -> dict[str, Any]:
        return self._agent_center.get_persona(target)

    async def publish(self, content: str, *, author: str = "workflowpy", reply_to: int | None = None) -> None:
        await self.forum.publish(author=author, content=content, reply_to=reply_to)

    async def send_agent(
        self,
        target: str,
        prompt: str,
        *,
        persona_tag: str | None = None,
        persona_override: str | None = None,
        session: str | None = None,
        connect_type: str | None = None,
        platform: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> SendToAgentResult:
        return await self._agent_center.send_agent(
            target,
            prompt,
            persona_tag=persona_tag,
            persona_override=persona_override,
            session=session,
            connect_type=connect_type,
            platform=platform,
            options=options,
        )

    async def send_persona(
        self,
        target: str,
        prompt: str,
        *,
        persona_override: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> SendToAgentResult:
        return await self._agent_center.send_persona(
            target,
            prompt,
            persona_override=persona_override,
            options=options,
        )

    def set_conclusion(self, text: str) -> None:
        self.forum.conclusion = text


class PythonWorkflowEngine:
    def __init__(
        self,
        forum: DiscussionForum,
        *,
        python_file: str,
        user_id: str = "anonymous",
        team: str = "",
    ):
        self.forum = forum
        self.python_file = python_file
        self._user_id = user_id
        self._team = team
        self.experts: list[Any] = []
        self.callback_url: str | None = None
        self.callback_session_id: str | None = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    async def _exec_script_main(self, ctx: PythonWorkflowContext) -> Any:
        try:
            with open(self.python_file, "r", encoding="utf-8") as f:
                source = f.read()
        except Exception as e:
            raise RuntimeError(f"无法读取 python workflow: {e}") from e

        wrapped = (
            "async def __workflowpy_main__():\n"
            + textwrap.indent(source, "    ")
            + "\n"
        )
        globals_dict: dict[str, Any] = {
            "__name__": "__main__",
            "__file__": self.python_file,
            "__package__": None,
            "__builtins__": __builtins__,
            "asyncio": asyncio,
            "json": json,
            "os": os,
            "sys": sys,
            "ctx": ctx,
            "question": ctx.question,
            "user_id": ctx.user_id,
            "team": ctx.team,
            "topic_id": self.forum.topic_id,
            "list_agents": ctx.list_agents,
            "list_personas": ctx.list_personas,
            "get_agent": ctx.get_agent,
            "get_persona": ctx.get_persona,
            "send_agent": ctx.send_agent,
            "send_persona": ctx.send_persona,
            "publish": ctx.publish,
            "set_conclusion": ctx.set_conclusion,
        }
        globals_dict["RESULT"] = None
        globals_dict["set_result"] = lambda value: globals_dict.__setitem__("RESULT", value)
        locals_dict: dict[str, Any] = {}
        try:
            code = compile(wrapped, self.python_file, "exec")
            exec(code, globals_dict, locals_dict)
            main_func = locals_dict.get("__workflowpy_main__") or globals_dict.get("__workflowpy_main__")
            if main_func is None or not callable(main_func):
                raise RuntimeError("workflowpy main wrapper missing")
            await main_func()
        except Exception as e:
            raise RuntimeError(f"workflowpy 脚本执行失败: {e}") from e
        return globals_dict.get("RESULT")

    async def run(self):
        self.forum.status = "discussing"
        self.forum.discussion = False
        self.forum.start_clock()
        self.forum.log_event("workflowpy_start", detail=os.path.basename(self.python_file))

        if self._cancelled:
            raise asyncio.CancelledError("workflow cancelled")

        ctx = PythonWorkflowContext(
            self.forum,
            user_id=self._user_id,
            team=self._team,
            question=self.forum.question,
        )
        result = await self._exec_script_main(ctx)

        if self._cancelled:
            raise asyncio.CancelledError("workflow cancelled")

        if self.forum.conclusion:
            conclusion = self.forum.conclusion
        elif result is None:
            conclusion = "workflowpy 执行完成"
        elif isinstance(result, str):
            conclusion = result
        else:
            conclusion = json.dumps(result, ensure_ascii=False, indent=2)

        self.forum.conclusion = conclusion
        self.forum.status = "concluded"
        self.forum.log_event("workflowpy_done", detail=os.path.basename(self.python_file))
