"""DeepSeek Responses API adapter for a tool-capable structured turn."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from jsonschema import validate
from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai.chat_models.base import _construct_responses_api_input
from openai import AsyncOpenAI

from webot.engine.tool_schema import drop_null_optionals, to_strict_parameters


def _base_url(model: Any) -> str:
    base = str(getattr(model, "api_base", "") or "").rstrip("/")
    parsed = urlparse(base)
    if parsed.hostname == "api.deepseek.com" and parsed.path.rstrip("/") in {"/beta", "/v1"}:
        return "https://api.deepseek.com"
    return base


def _response_tools(tools: list[dict]) -> list[dict]:
    result = []
    for tool in tools:
        function = tool.get("function") or {}
        if tool.get("type") != "function" or not function.get("name"):
            continue
        result.append({
            "type": "function", "name": function["name"],
            "description": function.get("description") or "",
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        })
    return result


async def deepseek_structured_turn(
    model: Any,
    messages: list[BaseMessage],
    response_format: dict,
    tools: list[dict] | None = None,
) -> AIMessage:
    """Send tools and a final text schema together, without a synthetic reply tool."""
    spec = response_format.get("json_schema") or {}
    schema = spec.get("schema")
    if response_format.get("type") != "json_schema" or not isinstance(schema, dict):
        raise ValueError("DeepSeek structured turn requires a JSON schema")
    strict_schema = to_strict_parameters(schema)
    secret = getattr(model, "openai_api_key", None)
    key = secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret or "")
    if not key:
        raise RuntimeError("DeepSeek API key is missing")
    client_kwargs = {"api_key": key, "base_url": _base_url(model)}
    http_client = getattr(model, "http_async_client", None)
    if http_client is not None:
        client_kwargs["http_client"] = http_client
    client = AsyncOpenAI(**client_kwargs)
    kwargs: dict[str, Any] = {
        "model": getattr(model, "model_name"),
        "input": _construct_responses_api_input(messages, store=False),
        "text": {"format": {
            "type": "json_schema", "name": str(spec.get("name") or "final_reply"),
            "schema": strict_schema,
        }},
        "store": False,
    }
    response_tools = _response_tools(tools or [])
    if response_tools:
        kwargs["tools"] = response_tools
        kwargs["tool_choice"] = "auto"
    max_tokens = getattr(model, "max_tokens", None)
    if max_tokens:
        kwargs["max_output_tokens"] = int(max_tokens)
    try:
        response = await client.responses.create(**kwargs)
    finally:
        # A supplied HTTP client belongs to the caller; closing AsyncOpenAI
        # would close it too and break later turns.
        if http_client is None:
            await client.close()
    if response.status != "completed":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        raise RuntimeError(f"DeepSeek response {response.status}: {reason or response.error}")
    calls = []
    for item in response.output or []:
        if item.type == "function_call":
            calls.append({
                "name": item.name, "args": json.loads(item.arguments),
                "id": item.call_id, "type": "tool_call",
            })
    content = response.output_text or ""
    if not calls:
        value = json.loads(content)
        validate(value, strict_schema)
        value = drop_null_optionals(value, schema)
        validate(value, schema)
        content = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    usage = getattr(response, "usage", None)
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
    usage_metadata = {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "input_token_details": {"cache_read": cached},
    }
    usage_metadata["total_tokens"] = usage_metadata["input_tokens"] + usage_metadata["output_tokens"]
    return AIMessage(content=content, tool_calls=calls, usage_metadata=usage_metadata)
