"""The shapes of an OASIS participant's reply: a post (with votes) or a selector's choice.

Passed as the ``response_format`` of an agent call; the agent gateway hands each
runtime the form it can enforce (see ``agents.gateway``).
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
