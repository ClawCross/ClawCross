"""
OpenAI 协议兼容辅助模块

提供 OpenAI API 兼容的消息格式转换和响应编码：
- 将 OpenAI 格式消息转换为 HumanMessage
- 构建 OpenAI 兼容的响应和流式 chunks
- 提取和格式化外部工具调用
"""

import json
import os
import time
from typing import Any, Callable, Dict, Optional, Set

from langchain_core.messages import AIMessage, HumanMessage

from agents.openai import ChatMessage


class OpenAIProtocolHelper:
    """OpenAI 协议兼容辅助类，负责消息转换和响应编码。"""

    def __init__(
        self,
        *,
        build_human_message: Callable[[str, Optional[list], Optional[list], Optional[list]], HumanMessage],
    ):
        self.build_human_message = build_human_message

    def openai_msg_to_human_message(self, msg: ChatMessage) -> HumanMessage:
        """将 OpenAI 格式的 ChatMessage 转换为 HumanMessage。

        :param msg: OpenAI 格式的聊天消息
        :return: LangChain HumanMessage 对象
        """
        content = msg.content
        if content is None:
            return HumanMessage(content="(空消息)")
        if isinstance(content, str):
            return HumanMessage(content=content)

        text_parts = []
        image_parts = []
        audio_parts = []
        file_parts = []
        for part in content:
            p = part if isinstance(part, dict) else part.dict()
            ptype = p.get("type", "")
            if ptype == "text":
                text_parts.append(p.get("text", ""))
            elif ptype == "image_url":
                image_parts.append(p)
            elif ptype == "input_audio":
                audio_parts.append(p.get("input_audio", {}))
            elif ptype == "file":
                file_parts.append(p)

        if not image_parts and not audio_parts and not file_parts:
            return HumanMessage(content="\n".join(text_parts) or "(空消息)")

        combined_text = "\n".join(text_parts)

        images = []
        for ip in image_parts:
            url = ip.get("image_url", {}).get("url", "")
            if url:
                images.append(url)

        audios = []
        for ad in audio_parts:
            audios.append({
                "base64": ad.get("data", ""),
                "format": ad.get("format", "webm"),
                "name": "recording.{fmt}".format(fmt=ad.get("format", "webm")),
            })

        media_exts = {
            ".avi",
            ".mp4",
            ".mkv",
            ".mov",
            ".webm",
            ".mp3",
            ".wav",
            ".flac",
            ".ogg",
            ".aac",
        }
        files = []
        for fp in file_parts:
            fdata = fp.get("file", {})
            fname = fdata.get("filename", "file")
            ext = os.path.splitext(fname)[1].lower()
            if fname.endswith(".pdf"):
                ftype = "pdf"
            elif ext in media_exts:
                ftype = "media"
            else:
                ftype = "text"
            files.append({
                "name": fname,
                "content": fdata.get("file_data", ""),
                "type": ftype,
            })

        return self.build_human_message(combined_text, images or None, files or None, audios or None)

    @staticmethod
    def extract_external_tool_names(tools: Optional[list]) -> Set[str]:
        """从工具定义列表中提取外部工具名称。

        :param tools: OpenAI 格式的工具定义列表
        :return: 外部工具名称集合
        """
        if not tools:
            return set()
        names: Set[str] = set()
        for tool in tools:
            if tool.get("type") == "function":
                names.add(tool["function"]["name"])
            elif tool.get("name"):
                names.add(tool["name"])
        return names

    @staticmethod
    def format_tool_calls_for_openai(ai_msg: AIMessage, external_names: Set[str]) -> Optional[list]:
        """将 AI 消息中的外部工具调用格式化为 OpenAI 格式。

        :param ai_msg: LangChain AI 消息
        :param external_names: 外部工具名称集合
        :return: OpenAI 格式的工具调用列表
        """
        if not hasattr(ai_msg, "tool_calls") or not ai_msg.tool_calls:
            return None
        external_calls = []
        for tc in ai_msg.tool_calls:
            if tc["name"] in external_names:
                external_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                    },
                })
        return external_calls or None

    @staticmethod
    def make_tool_calls_chunk(*, completion_id: str, model: str, tool_calls: list) -> str:
        """构建包含工具调用的 SSE chunk。

        :param completion_id: completion ID
        :param model: 模型名称
        :param tool_calls: 工具调用列表
        :return: SSE 格式的 chunk 字符串
        """
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": tool_calls},
                "finish_reason": "tool_calls",
            }],
        }
        return "data: {payload}\n\n".format(payload=json.dumps(chunk, ensure_ascii=False))
