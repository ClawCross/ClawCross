"""
会话摘要构建模块

从 LangGraph 消息列表中提取可展示文本，构建会话摘要：
- 首条人类消息（作为会话标题）
- 末条人类消息（作为最近活动）
- 消息计数
"""

from typing import Any, Iterable


def extract_human_content(raw: Any, list_fallback: str = "(图片消息)") -> str:
    """从 HumanMessage.content 中提取可展示文本。

    :param raw: 消息 content（可能是字符串或列表）
    :param list_fallback: 当无法提取文本时返回的默认值
    :return: 提取的文本字符串
    """
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        text = " ".join(
            p.get("text", "") for p in raw if isinstance(p, dict) and p.get("type") == "text"
        )
        return text or list_fallback
    return str(raw)


def iter_human_texts(
    messages: Iterable[Any],
    *,
    skip_prefixes: tuple[str, ...] = (),
    list_fallback: str = "(图片消息)",
):
    """遍历消息列表，产出过滤后的 HumanMessage 文本。

    :param messages: 消息迭代器
    :param skip_prefixes: 跳过以这些前缀开头的消息
    :param list_fallback: 无法提取文本时的默认值
    :yield: 过滤后的消息文本
    """
    for msg in messages:
        if type(msg).__name__ != "HumanMessage" or not hasattr(msg, "content"):
            continue
        content = extract_human_content(msg.content, list_fallback=list_fallback)
        if content and any(content.startswith(prefix) for prefix in skip_prefixes):
            continue
        yield content


def build_session_summary(
    messages: Iterable[Any],
    *,
    skip_prefixes: tuple[str, ...] = (),
    title_len: int = 50,
    last_len: int = 50,
    list_fallback: str = "(图片消息)",
) -> dict:
    """构建会话摘要（首条、末条、计数）。

    :param messages: 消息列表
    :param skip_prefixes: 跳过以这些前缀开头的消息
    :param title_len: 标题最大长度
    :param last_len: 最近消息最大长度
    :param list_fallback: 图片消息等无法提取时的占位文本
    :return: 包含 first_human、last_human、msg_count 的字典
    """
    first_human = ""
    last_human = ""
    msg_count = 0

    for content in iter_human_texts(
        messages,
        skip_prefixes=skip_prefixes,
        list_fallback=list_fallback,
    ):
        msg_count += 1
        if not first_human:
            first_human = content[:title_len]
        last_human = content[:last_len]

    return {
        "first_human": first_human,
        "last_human": last_human,
        "msg_count": msg_count,
    }


def first_human_title(
    messages: Iterable[Any],
    *,
    skip_prefixes: tuple[str, ...] = (),
    title_len: int = 80,
    list_fallback: str = "(图片消息)",
    default: str = "",
) -> str:
    """提取第一条可展示的 HumanMessage 文本作为会话标题。

    :param messages: 消息列表
    :param skip_prefixes: 跳过以这些前缀开头的消息
    :param title_len: 标题最大长度
    :param list_fallback: 无法提取时的默认值
    :param default: 无有效消息时返回的默认值
    :return: 会话标题字符串
    """
    for content in iter_human_texts(
        messages,
        skip_prefixes=skip_prefixes,
        list_fallback=list_fallback,
    ):
        if content:
            return content[:title_len]
    return default
