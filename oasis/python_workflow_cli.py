from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv

_SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
from utils.runtime_paths import ENV_FILE

load_dotenv(dotenv_path=ENV_FILE)

from integrations.agent_sender import SendToAgentResult
from oasis.agent_center import AgentCenter
from oasis.forum_client import conclude_topic, create_empty_topic, publish_to_topic, vote_topic_post

_TOPIC_POST_MAX_LEN = 8000
_TOPIC_POST_TRUNCATE_SUFFIX = "\n\n...[truncated by workflow runtime]"


def _update_meta_file(path: str, values: dict[str, Any]) -> None:
    if not path:
        return
    try:
        current: dict[str, Any] = {}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                current = loaded
        current.update(values)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except Exception:
        pass


def _parse_oasis_publish_payload(content: str) -> dict[str, Any] | None:
    raw = str(content or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("clawcross_type") != "oasis reply":
        return None
    return parsed


class StandaloneWorkflowContext:
    def __init__(
        self,
        *,
        user_id: str,
        team: str,
        question: str,
        run_id: str,
        auto_topic: bool = True,
        meta_file: str = "",
    ):
        self.user_id = user_id
        self.team = team
        self.question = question
        self.run_id = run_id
        self.auto_topic = auto_topic
        self.meta_file = meta_file
        self.topic_id: str | None = None
        self.conclusion: str = ""
        self.result: Any = None
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
        parsed = _parse_oasis_publish_payload(content)
        if parsed and self.topic_id:
            parsed_content = str(parsed.get("content") or "")
            parsed_reply_to = parsed.get("reply_to")
            if parsed_reply_to is None:
                parsed_reply_to = reply_to
            try:
                parsed_reply_to = int(parsed_reply_to) if parsed_reply_to is not None else None
            except Exception:
                parsed_reply_to = None

            entry = {
                "author": author,
                "content": parsed_content,
                "reply_to": parsed_reply_to,
                "votes": parsed.get("votes", []),
                "clawcross_type": "oasis reply",
            }
            self.published_messages.append(entry)
            print(f"[workflow:{author}] {parsed_content}", flush=True)

            mirror_content = parsed_content
            if len(mirror_content) > _TOPIC_POST_MAX_LEN:
                limit = _TOPIC_POST_MAX_LEN - len(_TOPIC_POST_TRUNCATE_SUFFIX)
                mirror_content = mirror_content[: max(0, limit)] + _TOPIC_POST_TRUNCATE_SUFFIX

            await publish_to_topic(
                topic_id=self.topic_id,
                user_id=self.user_id,
                author=author,
                content=mirror_content,
                reply_to=parsed_reply_to,
            )

            for vote in (parsed.get("votes") or []):
                if not isinstance(vote, dict):
                    continue
                target_post_id = vote.get("post_id")
                direction = str(vote.get("direction") or "").strip().lower()
                try:
                    target_post_id = int(target_post_id)
                except Exception:
                    continue
                if direction not in ("up", "down"):
                    continue
                await vote_topic_post(
                    topic_id=self.topic_id,
                    user_id=self.user_id,
                    post_id=target_post_id,
                    direction=direction,
                    voter=author,
                )
            return

        entry = {"author": author, "content": content, "reply_to": reply_to}
        self.published_messages.append(entry)
        print(f"[workflow:{author}] {content}", flush=True)
        if self.topic_id:
            mirror_content = content
            if len(mirror_content) > _TOPIC_POST_MAX_LEN:
                limit = _TOPIC_POST_MAX_LEN - len(_TOPIC_POST_TRUNCATE_SUFFIX)
                mirror_content = mirror_content[: max(0, limit)] + _TOPIC_POST_TRUNCATE_SUFFIX
            await publish_to_topic(
                topic_id=self.topic_id,
                user_id=self.user_id,
                author=author,
                content=mirror_content,
                reply_to=reply_to,
            )

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

    async def create_empty_topic(self, *, question: str, max_rounds: int = 1) -> dict[str, Any]:
        topic = await create_empty_topic(
            question=question,
            user_id=self.user_id,
            team=self.team or None,
            max_rounds=max_rounds,
        )
        self.topic_id = str(topic.get("topic_id") or "")
        if self.topic_id:
            _update_meta_file(self.meta_file, {"topic_id": self.topic_id})
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

    def set_conclusion(self, text: str) -> None:
        self.conclusion = text

    def set_result(self, value: Any) -> None:
        self.result = value


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a standalone ClawCross python workflow.")
    parser.add_argument("--question", default="", help="Question/task passed into the workflow.")
    parser.add_argument("--user-id", default="default", help="User ID for agent/topic scope.")
    parser.add_argument("--team", default="", help="Optional team scope.")
    parser.add_argument("--run-id", default="", help="Optional run ID supplied by the launcher.")
    parser.add_argument("--meta-file", default="", help="Optional metadata JSON file maintained by the launcher.")
    parser.add_argument("--result-file", default="", help="Optional JSON output file.")
    parser.add_argument(
        "--no-auto-topic",
        action="store_true",
        help="Disable the default behavior of auto-creating an OASIS topic for this run.",
    )
    return parser


async def _run(main_func: Callable[[StandaloneWorkflowContext], Awaitable[Any]]) -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    ctx = StandaloneWorkflowContext(
        user_id=args.user_id,
        team=args.team,
        question=args.question,
        run_id=args.run_id or uuid.uuid4().hex[:12],
        auto_topic=not args.no_auto_topic,
        meta_file=args.meta_file,
    )
    exit_code = 0
    try:
        if ctx.auto_topic:
            await ctx.create_empty_topic(
                question=ctx.question or "Standalone python workflow",
                max_rounds=1,
            )
        returned = await main_func(ctx)
        if ctx.result is None and returned is not None:
            ctx.result = returned
        if ctx.auto_topic and ctx.topic_id:
            conclusion = (ctx.conclusion or "").strip() or "Standalone python workflow finished."
            await ctx.conclude_topic(
                topic_id=ctx.topic_id,
                conclusion=conclusion,
                author="workflowpy",
            )
        payload = {
            "ok": True,
            "run_id": ctx.run_id,
            "question": ctx.question,
            "user_id": ctx.user_id,
            "team": ctx.team,
            "topic_id": ctx.topic_id,
            "result": ctx.result,
            "conclusion": ctx.conclusion,
            "published_messages": ctx.published_messages,
        }
    except Exception as e:
        exit_code = 1
        if ctx.auto_topic and ctx.topic_id:
            try:
                await ctx.publish(f"Workflow failed: {e}", author="workflowpy")
                await ctx.conclude_topic(
                    topic_id=ctx.topic_id,
                    conclusion=f"Workflow failed: {e}",
                    author="workflowpy",
                )
            except Exception:
                pass
        payload = {
            "ok": False,
            "run_id": ctx.run_id,
            "question": ctx.question,
            "user_id": ctx.user_id,
            "team": ctx.team,
            "topic_id": ctx.topic_id,
            "error": str(e),
            "published_messages": ctx.published_messages,
        }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.result_file:
        with open(args.result_file, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return exit_code


def run_cli(main_func: Callable[[StandaloneWorkflowContext], Awaitable[Any]]) -> int:
    return asyncio.run(_run(main_func))
