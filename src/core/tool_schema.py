"""Strict tool schemas: what a decoder can be constrained to, and what it sends back.

Strict tool calling (OpenAI / DeepSeek ``strict``, Anthropic ``strict``, Gemini
``VALIDATED``) compiles each tool's parameter schema into a grammar and decodes
the arguments inside it, so a call cannot come back malformed. The grammar only
accepts a closed subset of JSON Schema: every object lists all of its properties
as required and forbids extra ones, and there is nowhere to put a default.

``to_strict_parameters`` rewrites an MCP input schema into that subset without
changing what a call means: an optional argument stays optional by becoming
nullable (``null`` = "not given"), and its default moves into its description.
``drop_null_optionals`` undoes the encoding on the way back, removing the
``null`` for arguments the original schema did not require, so the tool's own
default applies exactly as it did before.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any
from urllib.parse import urlparse

_DROPPED_KEYS = ("title", "default", "examples", "$schema")


class StrictSchemaError(ValueError):
    """The schema has a part strict decoding cannot express (e.g. a free-form object)."""


def _resolve(node: Any, defs: dict[str, Any]) -> Any:
    """Inline ``$ref`` into ``#/$defs/...`` (non-recursive schemas only)."""
    if isinstance(node, list):
        return [_resolve(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        if not ref.startswith(("#/$defs/", "#/definitions/")) or name not in defs:
            raise StrictSchemaError(f"unresolvable $ref {ref!r}")
        merged = {**copy.deepcopy(defs[name]), **{k: v for k, v in node.items() if k != "$ref"}}
        return _resolve(merged, defs)
    return {k: _resolve(v, defs) for k, v in node.items() if k not in ("$defs", "definitions")}


def _allows_null(node: dict[str, Any]) -> bool:
    kind = node.get("type")
    if kind == "null" or (isinstance(kind, list) and "null" in kind):
        return True
    return any(isinstance(b, dict) and b.get("type") == "null" for b in node.get("anyOf") or [])


def _describe_default(node: dict[str, Any]) -> None:
    """Fold a meaningful ``default`` into the description before it is dropped."""
    if "default" not in node:
        return
    default = node["default"]
    # Empty and zero-like defaults read as "not set"; spelling them out costs
    # tokens on every request and tells the model nothing.
    if default in (None, "", [], {}, False, 0):
        return
    desc = str(node.get("description") or "")
    if "default" in desc.lower() or "默认" in desc:
        return
    rendered = json.dumps(default, ensure_ascii=False)
    cjk = any("一" <= ch <= "鿿" for ch in desc)
    note = f"默认 {rendered}" if cjk else f"default: {rendered}"
    node["description"] = f"{desc}（{note}）" if cjk else (f"{desc} ({note})" if desc else note)


def _strict(node: Any, path: str) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise StrictSchemaError(f"{path}: schema node is not an object")
    node = dict(node)
    _describe_default(node)
    for key in _DROPPED_KEYS:
        node.pop(key, None)

    all_of = node.pop("allOf", None)
    if all_of:
        if len(all_of) != 1:
            raise StrictSchemaError(f"{path}: allOf with {len(all_of)} branches")
        node = {**_strict(all_of[0], path), **node}

    if "anyOf" in node:
        node["anyOf"] = [_strict(branch, f"{path}|{i}") for i, branch in enumerate(node["anyOf"])]
        return node

    kind = node.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if "object" in kinds:
        properties = node.get("properties")
        extra = node.get("additionalProperties")
        if not properties and extra not in (None, False):
            raise StrictSchemaError(f"{path}: free-form object has no fixed properties")
        properties = properties or {}
        required = set(node.get("required") or [])
        new_props = {}
        for name, prop in properties.items():
            child = _strict(prop, f"{path}.{name}")
            if name not in required and not _allows_null(child):
                desc = child.pop("description", None)
                child = {"anyOf": [child, {"type": "null"}]}
                if desc:
                    child["description"] = desc
            new_props[name] = child
        node["properties"] = new_props
        node["required"] = list(new_props)
        node["additionalProperties"] = False
    if "array" in kinds:
        if not isinstance(node.get("items"), dict):
            raise StrictSchemaError(f"{path}: array without a single items schema")
        node["items"] = _strict(node["items"], f"{path}[]")
    if kind is None and "enum" not in node and "const" not in node:
        raise StrictSchemaError(f"{path}: node has no type")
    return node


def to_strict_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Return a strict-mode equivalent of *parameters*; raise ``StrictSchemaError`` if impossible."""
    if not isinstance(parameters, dict):
        raise StrictSchemaError("parameters is not an object schema")
    defs = {**(parameters.get("definitions") or {}), **(parameters.get("$defs") or {})}
    resolved = _resolve(parameters, defs)
    resolved.setdefault("type", "object")
    resolved.setdefault("properties", {})
    if resolved.get("type") != "object":
        raise StrictSchemaError("root schema must be an object")
    return _strict(resolved, "$")


def strict_violations(schema: Any, path: str = "$") -> list[str]:
    """Everything in *schema* a strict decoder would reject. Empty means compliant."""
    problems: list[str] = []
    if not isinstance(schema, dict):
        return [f"{path}: not an object"]
    if path == "$" and schema.get("type") != "object":
        problems.append("$: root must be type object")
    for key in ("$ref", "$defs", "definitions", "allOf", "oneOf", "not", *_DROPPED_KEYS):
        if key in schema:
            problems.append(f"{path}: keyword {key!r} not allowed")
    if "anyOf" in schema:
        for i, branch in enumerate(schema["anyOf"]):
            problems += strict_violations(branch, f"{path}|{i}")
        return problems
    kind = schema.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if kind is None and "enum" not in schema and "const" not in schema:
        problems.append(f"{path}: no type")
    if "object" in kinds:
        props = schema.get("properties")
        if not isinstance(props, dict):
            problems.append(f"{path}: object without properties")
            props = {}
        if schema.get("additionalProperties") is not False:
            problems.append(f"{path}: additionalProperties must be false")
        if sorted(schema.get("required") or []) != sorted(props):
            problems.append(f"{path}: every property must be required")
        for name, prop in props.items():
            problems += strict_violations(prop, f"{path}.{name}")
    if "array" in kinds:
        if not isinstance(schema.get("items"), dict):
            problems.append(f"{path}: array without items")
        else:
            problems += strict_violations(schema["items"], f"{path}[]")
    return problems


def _object_branch(node: Any, defs: dict[str, Any]) -> dict[str, Any] | None:
    """The (non-null) branch of *node* that describes a dict or list value, if any."""
    if not isinstance(node, dict):
        return None
    if "$ref" in node:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        return _object_branch(defs.get(name), defs)
    if node.get("allOf") and len(node["allOf"]) == 1:
        return _object_branch(node["allOf"][0], defs)
    for branch in node.get("anyOf") or []:
        found = _object_branch(branch, defs)
        if found is not None:
            return found
    if node.get("properties") is not None or node.get("items") is not None:
        return node
    return None


def drop_null_optionals(args: Any, schema: dict[str, Any] | None) -> Any:
    """Remove ``null`` values the strict encoding introduced for optional arguments.

    Walks *args* alongside the tool's original (non-strict) *schema*: a ``None``
    under a property the schema does not require is dropped, so the tool sees
    the argument as omitted and applies its own default. A ``None`` under a
    required property is kept — there it is a real value.
    """
    if not isinstance(schema, dict):
        return args
    defs = {**(schema.get("definitions") or {}), **(schema.get("$defs") or {})}

    def walk(value: Any, node: Any) -> Any:
        node = _object_branch(node, defs)
        if node is None:
            return value
        if isinstance(value, dict) and node.get("properties") is not None:
            required = set(node.get("required") or [])
            props = node["properties"]
            return {
                key: walk(val, props.get(key))
                for key, val in value.items()
                if not (val is None and key in props and key not in required)
            }
        if isinstance(value, list) and node.get("items") is not None:
            return [walk(item, node["items"]) for item in value]
        return value

    return walk(args, schema)


# --- Per-provider binding -----------------------------------------------------

DEEPSEEK_BETA_API_BASE = "https://api.deepseek.com/beta"


def strict_tool_mode() -> str:
    """``LLM_TOOL_STRICT``: ``auto`` (default) or ``on`` enable strict tool calling, ``off`` disables it."""
    value = (os.getenv("LLM_TOOL_STRICT") or "auto").strip().lower()
    return value if value in {"auto", "on", "off"} else "auto"


def strict_tool_binding(model: Any) -> tuple[Any, bool, dict[str, Any]]:
    """How to bind tools to *model* so their arguments are decoded under the schema.

    Returns ``(model, per_tool_strict, bind_kwargs)``:

    - OpenAI-protocol models and Anthropic: each function carries ``strict: true``
      (``per_tool_strict``), and ``bind_kwargs`` passes ``strict=True`` so the
      integration keeps it.
    - DeepSeek: only the beta endpoint enforces ``strict``, so a model pointed at
      the official API is moved there.
    - Gemini: no per-tool flag; ``VALIDATED`` function calling constrains every
      call to its declaration.
    """
    if strict_tool_mode() == "off":
        return model, False, {}

    cls_names = {cls.__name__ for cls in type(model).__mro__}
    if "ChatGoogleGenerativeAI" in cls_names:
        return model, False, {"tool_config": {"function_calling_config": {"mode": "VALIDATED"}}}
    if "ChatDeepSeek" in cls_names:
        base = str(getattr(model, "api_base", "") or "")
        parsed = urlparse(base)
        if parsed.hostname == "api.deepseek.com" and not parsed.path.rstrip("/").endswith("/beta"):
            # langchain_deepseek does this itself only for its exact default base
            # (".../v1"); ClawCross's default is the bare host, so do it here the
            # same way: copy, drop the clients, rebuild them for the new base.
            model = model.model_copy(update={
                "api_base": DEEPSEEK_BETA_API_BASE,
                "client": None,
                "async_client": None,
                "root_client": None,
                "root_async_client": None,
            }).validate_environment()
        return model, True, {"strict": True}
    if "ChatAnthropic" in cls_names or "BaseChatOpenAI" in cls_names:
        return model, True, {"strict": True}
    return model, False, {}


# --- Reply formats --------------------------------------------------------------

def _model_classes(model: Any) -> set[str]:
    return {cls.__name__ for cls in type(model).__mro__}


def reply_schema_hint(schema: dict[str, Any]) -> str:
    """Prompt text asking for one JSON object that matches *schema*."""
    return (
        "Reply with exactly one JSON object and nothing else (no prose, no code fence), "
        "matching this JSON schema:\n" + json.dumps(schema, ensure_ascii=False)
    )


def reply_format_binding(model: Any, response_format: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """How to ask *model* for a reply in an OpenAI ``response_format`` shape.

    Returns ``(bind_kwargs, prompt_hint)``:

    - OpenAI-protocol models take the format as given; ``json_schema`` is decoded
      under the schema.
    - DeepSeek rejects ``json_schema`` ("This response_format type is unavailable
      now"). Its ``json_object`` mode still decodes valid JSON, so the schema moves
      into the prompt, which that mode needs anyway: it only accepts prompts that
      ask for JSON.
    - Other integrations have no such request field; the prompt carries the schema.
    """
    kind = (response_format or {}).get("type")
    schema = ((response_format or {}).get("json_schema") or {}).get("schema")
    hint = reply_schema_hint(schema) if kind == "json_schema" and isinstance(schema, dict) else ""
    classes = _model_classes(model)
    if "ChatDeepSeek" in classes:
        if kind == "json_schema":
            return {"response_format": {"type": "json_object"}}, hint
        return {"response_format": response_format}, ""
    if "BaseChatOpenAI" in classes:
        return {"response_format": response_format}, ""
    return {}, hint


def forced_tool_choice_supported(model: Any) -> bool:
    """Whether *model* accepts a ``tool_choice`` that forces one particular tool.

    DeepSeek's thinking models answer 400 "Thinking mode does not support this
    tool_choice", and the model name alone does not tell which mode is on.
    """
    return "ChatDeepSeek" not in _model_classes(model)
