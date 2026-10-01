import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

"""
MCP Tool Server: LLM API Access

Provides tools for the Agent:

1. call_llm_api — Call any external OpenAI-compatible LLM API.
   Agent provides url, api_key, model, messages content, and gets back the response.
   Useful for consulting powerful/expensive models (GPT-5, Claude, etc.)

2. send_to_group — Reply in a group chat or one-to-one private chat.

Messages to another agent session go through webot's send_to_session.

Runs as a stdio MCP server.
"""

import os
import json
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from webot.mcp_tool_docs import DocumentedFastMCP as FastMCP
from common.runtime_paths import ENV_FILE

# 加载 .env
load_dotenv(dotenv_path=ENV_FILE)

mcp = FastMCP("LLM API Access")

# Internal Agent endpoint
_AGENT_PORT = os.getenv("PORT_AGENT", "51200")
_INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")

# Default timeout for external API calls (seconds)
_DEFAULT_TIMEOUT = 120
# Default timeout for internal sync calls

@mcp.tool()
async def call_llm_api(
    username: str,
    api_url: str,
    api_key: str,
    model: str,
    content: str,
    system_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """
    Call an external OpenAI-compatible chat completions API and return its
    reply — for a second opinion, or a stronger model on hard reasoning.

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        api_url: Full API endpoint URL, e.g. "https://api.openai.com/v1/chat/completions"
        api_key: API key / Bearer token for authentication
        model: Model name, e.g. "gpt-5", "claude-sonnet-4-20250514"
        content: The user message content to send to the model
        system_prompt: Optional system prompt to prepend
        temperature: Sampling temperature (0-2), default 0.7
        max_tokens: Maximum response tokens, default 4096
        timeout: Request timeout in seconds, default 120

    Returns:
        The model's response text, or an error message
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # 推理模型（o1/o3/o4 系列）不支持自定义 temperature，只能用默认值
    _model_lower = model.lower()
    _is_reasoning = any(
        _model_lower.startswith(p) and (len(_model_lower) == len(p) or _model_lower[len(p)] in "-_.")
        for p in ("o1", "o3", "o4")
    )
    if not _is_reasoning:
        payload["temperature"] = temperature

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if response.status_code != 200:
                return (
                    f"❌ API 请求失败 (HTTP {response.status_code}):\n"
                    f"{response.text[:2000]}"
                )

            res_data = response.json()

            # Standard OpenAI format
            if "choices" in res_data and res_data["choices"]:
                reply = res_data["choices"][0].get("message", {}).get("content", "")
                usage = res_data.get("usage", {})
                usage_info = ""
                if usage:
                    usage_info = (
                        f"\n\n📊 Token 用量: "
                        f"prompt={usage.get('prompt_tokens', '?')}, "
                        f"completion={usage.get('completion_tokens', '?')}, "
                        f"total={usage.get('total_tokens', '?')}"
                    )
                return f"✅ [{model}] 回复:\n\n{reply}{usage_info}"

            # Fallback: return raw response
            return f"⚠️ 非标准响应格式:\n{json.dumps(res_data, ensure_ascii=False, indent=2)[:3000]}"

    except httpx.TimeoutException:
        return f"❌ 请求超时 ({timeout}s)。可以增大 timeout 参数重试。"
    except Exception as e:
        return f"❌ 请求异常: {type(e).__name__}: {str(e)}"

@mcp.tool()
async def send_to_group(
    username: str,
    group_id: str,
    content: str,
    source_session: str = "",
    expected_title: str | None = None,
) -> str:
    """
    Post into a group chat or private chat you are a member of: the reply to a
    message marked "[群聊 …]" or "[私聊 …]" (people only see what is posted
    here), or speaking up on your own.

    Args:
        username: (auto-injected) current user identity; do NOT set manually
        group_id: The group_id given in the message you are answering
        content: The message. Write @name to wake that member; without an @mention,
            other agents are not woken. Only the group owner or main agent may
            use @所有人. Do not expose internal session or agent IDs in the post.
            When sharing a local file for preview or download, send its absolute
            path on its own line without a code block or extra explanation.
        source_session: (auto-injected) current session ID; do NOT set manually
        expected_title: Optional exact group name to verify against group_id; query
            list_agent_groups or get_group_details before choosing an unfamiliar target.

    Returns:
        Confirmation of message delivery
    """
    if not _INTERNAL_TOKEN:
        return "❌ 系统未配置 INTERNAL_TOKEN，无法发送群聊消息。"
    if not source_session or not (group_id or "").strip():
        return "❌ 缺少调用 agent 身份或群号，不能以用户身份代发。"
    from webot.mcp.caller_agent import internal_headers

    agent = source_session  # the session is the agent
    gid = quote((group_id or "").strip(), safe="")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"http://127.0.0.1:{_AGENT_PORT}/groups/{gid}/messages",
                headers=internal_headers(username),
                json={"content": content, "agent": agent, "expected_title": expected_title},
            )
            if response.status_code != 200:
                return f"❌ 发送失败 (HTTP {response.status_code}): {response.text[:500]}"
            return f"✅ 消息已发送到群聊 [{group_id}]"
    except Exception as e:
        return f"❌ 发送群聊消息失败: {type(e).__name__}: {str(e)}"

async def _read_context(username: str, path: str, **params) -> dict:
    from webot.mcp.caller_agent import internal_headers
    if not _INTERNAL_TOKEN:
        raise RuntimeError("系统未配置 INTERNAL_TOKEN")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"http://127.0.0.1:{_AGENT_PORT}{path}",
                                    headers=internal_headers(username), params=params)
    response.raise_for_status()
    return response.json()


@mcp.tool()
async def list_agent_groups(username: str, source_session: str = "") -> str:
    """List this agent's live group/private-chat membership, group IDs, names and roles.
    username and source_session are injected; do not set them. No conversation bodies.
    Use these exact group IDs when choosing where to send a message.
    """
    if not source_session:
        return "❌ 缺少调用 agent 身份。"
    try:
        return json.dumps(await _read_context(username, '/groups', agent_id=source_session), ensure_ascii=False)
    except Exception as exc:
        return f"❌ 查询群归属失败: {exc}"


@mcp.tool()
async def get_group_details(username: str, group_id: str, source_session: str = "") -> str:
    """Read one of this agent's groups: exact group ID/title, owner, members, roles and reply channel.
    No conversation bodies. username/source_session are injected. Non-members cannot read it.

    Args:
        group_id: Exact group ID from the live membership list, never a guessed name.
    """
    if not source_session:
        return "❌ 缺少调用 agent 身份。"
    try:
        groups = (await _read_context(username, '/groups', agent_id=source_session)).get('groups', [])
        group = next((g for g in groups if g['group_id'] == group_id), None)
        return json.dumps(group, ensure_ascii=False) if group else "❌ 你不在这个群里，或群已删除。"
    except Exception as exc:
        return f"❌ 查询群详情失败: {exc}"


@mcp.tool()
async def get_team_details(username: str, team: str) -> str:
    """Read an owned team's agents, IDs, roles and lead. username is injected.
    Call when team details are needed; do not infer roles from old conversation history.

    Args:
        team: Exact owned team namespace shown in the current team metadata.
    """
    try:
        return json.dumps(await _read_context(username, '/v1/teams/' + quote(team, safe='')), ensure_ascii=False)
    except Exception as exc:
        return f"❌ 查询 team 详情失败: {exc}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
