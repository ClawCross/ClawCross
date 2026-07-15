from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
import uuid
from typing import Any

_SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from integrations.agent_sender import SendToAgentResult
from utils.runtime_paths import DATA_DIR, USER_FILES_DIR
from oasis.agent_center import AgentCenter
from oasis.forum import DiscussionForum
from oasis.forum_client import conclude_topic, create_empty_topic, publish_to_topic

_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


def workflow_python_dir(user_id: str, team: str = "") -> str:
    if team:
        return os.path.join(str(USER_FILES_DIR), user_id, "teams", team, "oasis", "python")
    return os.path.join(str(USER_FILES_DIR), user_id, "oasis", "python")


def python_workflow_runs_dir() -> str:
    path = os.path.join(str(DATA_DIR), "python_workflow_runs")
    os.makedirs(path, exist_ok=True)
    return path


def resolve_python_workflow_path(user_id: str, python_file: str, team: str = "") -> tuple[str | None, str | None]:
    if not python_file:
        return None, "未提供 python workflow 文件名"
    target_name = python_file if python_file.endswith(".py") else f"{python_file}.py"
    matches: list[tuple[str, str]] = []
    user_root = os.path.join(str(USER_FILES_DIR), user_id)
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

    async def send_agent_once(
        self,
        target: str = "",
        prompt: str | None = None,
        *,
        persona_tag: str | None = None,
        persona_override: str | None = None,
        connect_type: str | None = None,
        platform: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> SendToAgentResult:
        return await self._agent_center.send_agent_once(
            target,
            prompt,
            persona_tag=persona_tag,
            persona_override=persona_override,
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

    async def call_llm(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> SendToAgentResult:
        return await self._agent_center.call_llm(
            prompt,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens,
            options=options,
        )

    def set_conclusion(self, text: str) -> None:
        self.forum.conclusion = text


class StandalonePythonWorkflowContext:
    def __init__(self, *, user_id: str, team: str, question: str):
        self.user_id = user_id
        self.team = team
        self.question = question
        self.topic_id: str | None = None
        self.conclusion: str = ""
        self.published_messages: list[dict[str, Any]] = []
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
        entry = {"author": author, "content": content, "reply_to": reply_to}
        self.published_messages.append(entry)
        print(f"[workflowpy:{author}] {content}", flush=True)

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

    async def send_agent_once(
        self,
        target: str = "",
        prompt: str | None = None,
        *,
        persona_tag: str | None = None,
        persona_override: str | None = None,
        connect_type: str | None = None,
        platform: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> SendToAgentResult:
        return await self._agent_center.send_agent_once(
            target,
            prompt,
            persona_tag=persona_tag,
            persona_override=persona_override,
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

    async def call_llm(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> SendToAgentResult:
        return await self._agent_center.call_llm(
            prompt,
            temperature=temperature,
            model=model,
            max_tokens=max_tokens,
            options=options,
        )

    def set_conclusion(self, text: str) -> None:
        self.conclusion = text

    async def create_empty_topic(self, *, question: str, max_rounds: int = 1) -> dict[str, Any]:
        topic = await create_empty_topic(
            question=question,
            user_id=self.user_id,
            team=self.team or None,
            max_rounds=max_rounds,
        )
        self.topic_id = str(topic.get("topic_id") or "")
        return topic

    async def publish_to_topic(
        self,
        *,
        topic_id: str,
        author: str,
        content: str,
        reply_to: int | None = None,
    ) -> dict[str, Any]:
        return await publish_to_topic(
            topic_id=topic_id,
            user_id=self.user_id,
            author=author,
            content=content,
            reply_to=reply_to,
        )

    async def conclude_topic(
        self,
        *,
        topic_id: str,
        conclusion: str,
        author: str | None = None,
    ) -> dict[str, Any]:
        return await conclude_topic(
            topic_id=topic_id,
            user_id=self.user_id,
            conclusion=conclusion,
            author=author,
        )


async def _execute_python_workflow_source(
    *,
    source: str,
    python_file: str,
    globals_dict: dict[str, Any],
) -> Any:
    wrapped = (
        "async def __workflowpy_main__():\n"
        + textwrap.indent(source, "    ")
        + "\n"
    )
    globals_dict["RESULT"] = None
    globals_dict["set_result"] = lambda value: globals_dict.__setitem__("RESULT", value)
    locals_dict: dict[str, Any] = {}
    code = compile(wrapped, python_file, "exec")
    exec(code, globals_dict, locals_dict)
    main_func = locals_dict.get("__workflowpy_main__") or globals_dict.get("__workflowpy_main__")
    if main_func is None or not callable(main_func):
        raise RuntimeError("workflowpy main wrapper missing")
    await main_func()
    return globals_dict.get("RESULT")


async def run_python_workflow_standalone(
    *,
    python_file: str,
    user_id: str = "anonymous",
    team: str = "",
    question: str = "",
) -> dict[str, Any]:
    try:
        with open(python_file, "r", encoding="utf-8") as f:
            source = f.read()
    except Exception as e:
        raise RuntimeError(f"无法读取 python workflow: {e}") from e

    ctx = StandalonePythonWorkflowContext(
        user_id=user_id,
        team=team,
        question=question,
    )
    run_id = uuid.uuid4().hex[:12]
    globals_dict: dict[str, Any] = {
        "__name__": "__main__",
        "__file__": python_file,
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
        "topic_id": ctx.topic_id,
        "run_id": run_id,
        "create_empty_topic": ctx.create_empty_topic,
        "publish_to_topic": ctx.publish_to_topic,
        "conclude_topic": ctx.conclude_topic,
        "list_agents": ctx.list_agents,
        "list_personas": ctx.list_personas,
        "get_agent": ctx.get_agent,
        "get_persona": ctx.get_persona,
        "send_agent": ctx.send_agent,
        "send_agent_once": ctx.send_agent_once,
        "send_persona": ctx.send_persona,
        "call_llm": ctx.call_llm,
        "publish": ctx.publish,
        "set_conclusion": ctx.set_conclusion,
        "AgentCenter": AgentCenter,
    }
    result = await _execute_python_workflow_source(
        source=source,
        python_file=python_file,
        globals_dict=globals_dict,
    )
    return {
        "ok": True,
        "mode": "standalone",
        "run_id": run_id,
        "python_file": python_file,
        "question": question,
        "user_id": user_id,
        "team": team,
        "topic_id": ctx.topic_id,
        "result": result,
        "conclusion": ctx.conclusion,
        "published_messages": ctx.published_messages,
    }


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
            "send_agent_once": ctx.send_agent_once,
            "send_persona": ctx.send_persona,
            "call_llm": ctx.call_llm,
            "publish": ctx.publish,
            "set_conclusion": ctx.set_conclusion,
        }
        try:
            return await _execute_python_workflow_source(
                source=source,
                python_file=self.python_file,
                globals_dict=globals_dict,
            )
        except Exception as e:
            raise RuntimeError(f"workflowpy 脚本执行失败: {e}") from e

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
