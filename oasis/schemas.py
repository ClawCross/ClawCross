"""Structured-output schemas for OASIS expert replies.

These are handed to LangChain's ``with_structured_output()`` so the
provider's own constrained-decoding / forced-tool-call mechanism guarantees
a conformant reply, instead of asking the model in prose to produce JSON
and then parsing free text with brace-scanning and regex fallbacks.

Only usable where the expert backend is a direct chat-model call
(``services.llm_factory.create_chat_model``). Backends that proxy to a
tool-using agent runtime (SessionExpert) or to an external CLI/ACP process
(ExternalExpert) have no such hook and keep the prose+parser path.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OasisVote(BaseModel):
    post_id: int
    direction: Literal["up", "down"]


class OasisReplyOut(BaseModel):
    """讨论/执行模式下的标准发言协议。"""

    clawcross_type: Literal["oasis reply"] = "oasis reply"
    reply_to: int | None = Field(
        default=None,
        description="要回复的帖子ID；论坛为空或本轮无新增内容时为 null",
    )
    content: str = Field(description="发言内容或执行结果")
    votes: list[OasisVote] = Field(
        default_factory=list,
        description="对其他帖子的投票；没有要投票的帖子时为空列表",
    )


class OasisChooseOut(BaseModel):
    """选择器节点的路径选择协议。"""

    clawcross_type: Literal["oasis choose"] = "oasis choose"
    choose: int = Field(description="选择的路径编号，对应给出的选项编号")
    content: str = Field(default="", description="选择理由（可选）")


def _to_strict_json_schema(schema: dict) -> dict:
    """Rewrite a Pydantic JSON schema into OpenAI structured-outputs' strict
    subset: every object marks all its properties required and forbids
    extras, recursively (including nested $defs).  Pydantic's own output
    leaves fields with defaults out of "required" and never sets
    additionalProperties, which strict mode rejects.
    """

    def walk(node):
        if isinstance(node, dict):
            node.pop("default", None)
            props = node.get("properties")
            if isinstance(props, dict):
                node["required"] = list(props.keys())
                node["additionalProperties"] = False
                for v in props.values():
                    walk(v)
            for key in ("$defs", "definitions"):
                if isinstance(node.get(key), dict):
                    for v in node[key].values():
                        walk(v)
            for key in ("items", "anyOf", "allOf", "oneOf"):
                v = node.get(key)
                if isinstance(v, list):
                    for x in v:
                        walk(x)
                elif isinstance(v, dict):
                    walk(v)
        return node

    return walk(dict(schema))


def to_openai_response_format(model_cls: type[BaseModel], *, strict: bool = True) -> dict:
    """Build an OpenAI ``response_format`` envelope enforcing *model_cls*.

    Used for backends that reach the LLM through the WeBot chat-completions
    endpoint (``core/agent.py``'s additive ``response_format`` binding)
    rather than a direct ``with_structured_output()`` call.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model_cls.__name__,
            "schema": _to_strict_json_schema(model_cls.model_json_schema()),
            "strict": strict,
        },
    }
