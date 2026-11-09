#!/usr/bin/env python3
"""ClawCross Creator Service — convert open-web organizational structures into Clawcross teams.

Three-stage pipeline:
  1. Discovery  — No TINYFISH_API_KEY: one LLM call, task → roles only (no URL list).
                  With key: LLM URL hints, then TinyFish browser if URLs empty.
  2. Extraction — Requires TinyFish to open URLs; optional LLM fallback only if TinyFish fails after a key was set.
  3. Mapping    — AI-powered conversion to Clawcross personas + LLM DAG enhancement + YAML workflows + ZIP snapshot

Shared by:
- Flask REST endpoints (front.py)
- Future CLI / agent integration
"""

from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import time
import utils.scheduler_service
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

# Reuse TinyFish client infrastructure
from services.tinyfish_monitor_service import (
    TinyFishClient,
    Target,
    create_client,
    get_api_key,
    get_base_url,
    iter_sse_json_events,
    DEFAULT_REQUEST_TIMEOUT,
)
from utils.runtime_paths import DATA_DIR as RUNTIME_DATA_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = DATA_DIR / "prompts"
DEFAULT_JOBS_DB_PATH = RUNTIME_DATA_DIR / "team_creator_jobs.db"


def get_jobs_db_path(db_path: str | Path | None = None) -> Path:
    explicit = db_path if db_path is not None else os.getenv("TEAM_CREATOR_JOBS_DB_PATH", str(DEFAULT_JOBS_DB_PATH))
    return Path(explicit)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _connect_jobs_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = get_jobs_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS team_creator_jobs (
            job_id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL DEFAULT '',
            task_description TEXT NOT NULL,
            team_name TEXT NOT NULL,
            status TEXT NOT NULL,
            discovered_pages_json TEXT NOT NULL DEFAULT '[]',
            extracted_roles_json TEXT NOT NULL DEFAULT '[]',
            team_config_json TEXT NOT NULL DEFAULT '{}',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_team_creator_jobs_owner_created
        ON team_creator_jobs(owner_id, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_team_creator_jobs_status
        ON team_creator_jobs(status)
        """
    )
    return conn

# ──────────────────────────────────────────────────────────────
# Preset Expert Pool — for smart matching
# ──────────────────────────────────────────────────────────────

def _load_preset_tags() -> dict[str, dict]:
    """Load all preset expert tags (public + agency) for matching.

    Preserves name_zh, category, description fields for richer matching
    so that _match_preset() can search across all 70+ experts.
    """
    pool: dict[str, dict] = {}

    # Public experts
    public_path = PROMPTS_DIR / "oasis_experts.json"
    if public_path.exists():
        try:
            for item in json.loads(public_path.read_text("utf-8")):
                pool[item["tag"]] = {
                    "name": item.get("name", item["tag"]),
                    "name_zh": item.get("name", ""),
                    "name_en": item.get("name_en", ""),
                    "tag": item["tag"],
                    "persona": item.get("persona", ""),
                    "temperature": item.get("temperature", 0.7),
                    "source": "public",
                    "category": "_public",
                    "description": "",
                }
        except Exception:
            pass

    # Agency experts — preserve category + description for matching
    agency_path = PROMPTS_DIR / "agency_experts.json"
    if agency_path.exists():
        try:
            for item in json.loads(agency_path.read_text("utf-8")):
                tag = item.get("tag", "")
                if tag and tag not in pool:
                    # Try to load .md persona file for richer matching
                    persona = ""
                    prompt_file = item.get("prompt_file", "")
                    if prompt_file:
                        md_path = PROMPTS_DIR / "agency_agents" / prompt_file
                        if md_path.exists():
                            try:
                                persona = md_path.read_text("utf-8")
                            except Exception:
                                pass
                    pool[tag] = {
                        "name": item.get("name", tag),
                        "name_zh": item.get("name_zh", ""),
                        "name_en": item.get("name", ""),
                        "tag": tag,
                        "persona": persona,
                        "temperature": item.get("temperature", 0.7),
                        "source": "agency",
                        "category": item.get("category", ""),
                        "description": item.get("description", ""),
                    }
        except Exception:
            pass

    return pool


PRESET_POOL = _load_preset_tags()


# ──────────────────────────────────────────────────────────────
# Stage 1: Discovery — LLM-powered URL discovery (no TinyFish)
# ──────────────────────────────────────────────────────────────

DISCOVERY_LLM_PROMPT = (
    "You are a research assistant helping to find high-quality web pages "
    "about team structures and SOPs for a specific task.\n\n"
    "Task: {task_description}\n\n"
    "Search and identify the top 3-5 most authoritative URLs that describe "
    "the standard operating procedure (SOP) or organizational team structure "
    "for this task. Focus on pages that list specific roles, their responsibilities, "
    "and the workflow sequence. Avoid generic blog posts; prefer:\n"
    "- Official documentation and templates (e.g. Notion, Atlassian)\n"
    "- Job hierarchy and organizational chart pages\n"
    "- Industry SOP references and best practices\n"
    "- LinkedIn or job description pages with team structures\n\n"
    "Return ONLY valid JSON, no markdown:\n"
    '{{"source_urls": [{{"url": "https://...", "title": "...", '
    '"type": "org_chart|sop|team_page|job_desc", "relevance": "..."}}]}}'
)


def discover_urls_via_llm(task_description: str) -> list[dict]:
    """Use the internal LLM to discover relevant URLs for SOP/org pages.

    Returns a list of {url, title, type, relevance} dicts.
    This replaces the old approach of sending TinyFish to google.com blindly.
    """
    from services.llm_factory import create_chat_model

    llm = create_chat_model(temperature=0.3, max_tokens=2048, timeout=60)
    prompt = DISCOVERY_LLM_PROMPT.format(task_description=task_description)

    response = llm.invoke(prompt)
    raw_content = response.content if hasattr(response, "content") else str(response)

    # Handle OpenAI Responses API format where content is a list of blocks
    if isinstance(raw_content, list):
        text_parts = []
        for block in raw_content:
            if isinstance(block, dict) and block.get("text"):
                text_parts.append(block["text"])
            elif isinstance(block, str):
                text_parts.append(block)
        raw_text = "\n".join(text_parts)
    else:
        raw_text = str(raw_content)

    # Parse JSON from response (may be wrapped in markdown code blocks)
    clean = raw_text.strip()
    if clean.startswith("```"):
        # Remove ```json ... ``` wrapper
        clean = re.sub(r"^```\w*\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean)
        clean = clean.strip()

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            return []

    urls = data.get("source_urls") or data.get("pages") or data.get("urls") or []
    if not isinstance(urls, list):
        return []

    result = []
    for item in urls:
        if isinstance(item, str):
            result.append({"url": item, "title": "", "type": "page", "relevance": ""})
        elif isinstance(item, dict) and item.get("url"):
            result.append({
                "url": item["url"],
                "title": item.get("title", ""),
                "type": item.get("type", "page"),
                "relevance": item.get("relevance", ""),
            })

    return result


# Keep the old TinyFish discovery as fallback / alternative
DISCOVERY_GOAL_TEMPLATE = (
    "Search the web for organizational structure, SOP (Standard Operating Procedures), "
    "or team composition information related to: {task_description}\n\n"
    "Find 3-8 relevant pages that describe:\n"
    "- Team roles and responsibilities\n"
    "- Organizational hierarchies\n"
    "- Workflow processes and SOPs\n"
    "- Team member descriptions and skill requirements\n\n"
    "Return strict JSON only:\n"
    '{{"pages": [{{"url": "", "title": "", "relevance": "", "type": "org_chart|sop|team_page|job_desc"}}]}}'
)


def build_discovery_target(task_description: str, search_url: str = "") -> Target:
    """Create a TinyFish target for the discovery phase."""
    url = search_url or "https://www.google.com"
    goal = DISCOVERY_GOAL_TEMPLATE.format(task_description=task_description)
    return Target(
        site_key="team-creator-discovery",
        name="ClawCross Creator Discovery",
        url=url,
        goal=goal,
        browser_profile="lite",
    )


def stream_discovery(task_description: str, search_url: str = "") -> Iterator[dict[str, Any]]:
    """Discovery: without TinyFish key = one LLM step (task → roles only, no URLs).

    With TinyFish: LLM URL hints → pages, or empty URLs → TinyFish browser (unchanged).
    """
    if not get_api_key():
        yield {"type": "STARTED", "message": "纯 LLM 模式：根据任务描述一次生成角色（无 URL / 无浏览器）…"}
        yield {"type": "PROGRESS", "message": "正在生成与后续构建一致的角色 JSON…"}
        try:
            roles_out = generate_roles_from_task_via_llm(task_description)
        except Exception as ex:
            yield {"type": "ERROR", "error": f"LLM 生成角色失败：{ex}"}
            return
        if not roles_out:
            yield {"type": "ERROR", "error": "LLM 未返回可用角色，请修改任务描述后重试。"}
            return
        yield {
            "type": "COMPLETE",
            "message": f"已生成 {len(roles_out)} 个角色",
            "result": json.dumps({
                "pages": [],
                "roles": roles_out,
                "llm_direct": True,
            }, ensure_ascii=False),
        }
        return

    yield {"type": "STARTED", "message": "正在使用 AI 搜索相关 SOP / 组织架构 URL..."}

    try:
        urls = discover_urls_via_llm(task_description)
        if urls:
            yield {
                "type": "PROGRESS",
                "message": f"AI 发现了 {len(urls)} 个候选页面",
            }
            yield {
                "type": "COMPLETE",
                "message": f"发现 {len(urls)} 个候选 URL",
                "result": json.dumps({"pages": urls}, ensure_ascii=False),
            }
            return
        yield {
            "type": "PROGRESS",
            "message": "AI 未找到候选 URL，切换到 TinyFish 浏览器搜索...",
        }
    except Exception as e:
        yield {
            "type": "PROGRESS",
            "message": f"AI URL 发现失败 ({e})，切换到 TinyFish 浏览器搜索...",
        }

    target = build_discovery_target(task_description, search_url)
    client = create_client(request_timeout=300)
    yield from client.run_sse(target)


# ──────────────────────────────────────────────────────────────
# Stage 2: Extraction — crawl discovered pages for role data
# ──────────────────────────────────────────────────────────────

EXTRACTION_GOAL_TEMPLATE = (
    "Analyze the content of this page and extract the organizational roles "
    "and their interaction logic into a structured JSON format for a multi-agent system.\n\n"
    "For each role discovered, provide:\n"
    "1. role_name: A concise title for the role (the official position name)\n"
    "2. personality_traits: Personality and tone inferred from the responsibilities "
    "(e.g. analytical, detail-oriented, creative, empathetic)\n"
    "3. primary_responsibilities: A list of 3-5 key tasks this role performs\n"
    "4. input_dependency: Which other role(s) provide the information this role needs "
    "(use the role_name of those roles)\n"
    "5. output_target: Who does this role hand off completed work to "
    "(use the role_name of downstream roles)\n"
    "6. tools_used: Any tools, software, or platforms mentioned for this role\n\n"
    "Constraint: Output MUST be a strictly formatted JSON array. "
    "Do NOT include any explanation or markdown formatting.\n"
    "Return ONLY valid JSON:\n"
    '{{"roles": [{{"role_name": "", "personality_traits": [], '
    '"primary_responsibilities": [], "input_dependency": [], '
    '"output_target": [], "tools_used": []}}]}}'
)

EXTRACTION_LLM_PAGE_PREAMBLE = (
    "You are helping Clawcross ClawCross Creator extract organizational roles for a multi-agent workflow.\n"
    "There is no live browser; infer reasonable roles from the URL and page title only.\n"
    "Do not invent verbatim quotes from the page. If unsure, propose a small generic role set.\n\n"
    "Target:\n"
    "- URL: {page_url}\n"
    "- Title: {page_title}\n\n"
)


def build_extraction_targets(pages: list[dict]) -> list[Target]:
    """Create TinyFish targets for parallel extraction from discovered pages."""
    targets = []
    for i, page in enumerate(pages):
        url = page.get("url", "")
        if not url:
            continue
        title = page.get("title", f"Page {i + 1}")
        targets.append(Target(
            site_key=f"team-creator-extract-{i}",
            name=f"Extract: {title[:50]}",
            url=url,
            goal=EXTRACTION_GOAL_TEMPLATE,
            browser_profile="lite",
        ))
    return targets


_NO_TF_URL_EXTRACT_MSG = (
    "未配置 TINYFISH_API_KEY，无法按 URL 打开页面提取。请在上一步用「任务描述」走纯 LLM 一次生成角色，"
    "或配置 TinyFish 后再使用链接提取。"
)


def run_extraction_batch(pages: list[dict]) -> list[dict[str, Any]]:
    """Parallel extraction via TinyFish. No key → cannot fetch URLs; returns failed rows (no fake per-URL LLM)."""
    targets = build_extraction_targets(pages)
    if not targets:
        return []

    if not get_api_key():
        return [
            {
                "run_id": f"no-tinyfish-{i}",
                "target": t.name,
                "url": t.url,
                "status": "FAILED",
                "result": None,
                "error": _NO_TF_URL_EXTRACT_MSG,
            }
            for i, t in enumerate(targets)
        ]

    results: list[dict[str, Any]] = []
    try:
        client = create_client(request_timeout=300)
        run_ids = client.start_batch(targets)
        pending = dict(zip(run_ids, targets))
        max_wait = 600  # 10 minutes
        start = time.time()

        while pending and (time.time() - start) < max_wait:
            runs_data = client.get_runs_batch(list(pending.keys()))
            for run in runs_data:
                run_id = str(run.get("run_id", ""))
                status = str(run.get("status", "")).upper()
                if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                    results.append({
                        "run_id": run_id,
                        "target": pending[run_id].name,
                        "url": pending[run_id].url,
                        "status": status,
                        "result": run.get("result"),
                        "error": run.get("error"),
                    })
                    pending.pop(run_id, None)
            if pending:
                time.sleep(5)
    except Exception as e:
        _log.warning("TinyFish batch extraction failed, using LLM fallback: %s", e)
        return _run_extraction_batch_llm(targets)

    return results


def stream_extraction(page_url: str, page_title: str = "") -> Iterator[dict[str, Any]]:
    """Extract one page: TinyFish SSE when API key set; no key → URL 提取不可用（纯 LLM 只走 discover）。"""
    if not get_api_key():
        yield {"type": "PROGRESS", "message": _NO_TF_URL_EXTRACT_MSG}
        yield {"type": "ERROR", "error": _NO_TF_URL_EXTRACT_MSG}
        return
    target = Target(
        site_key="team-creator-extract",
        name=f"Extract: {(page_title or page_url)[:50]}",
        url=page_url,
        goal=EXTRACTION_GOAL_TEMPLATE,
        browser_profile="lite",
    )
    try:
        client = create_client(request_timeout=300)
        yield from client.run_sse(target)
    except Exception as exc:
        _log.warning("TinyFish extraction failed for %s, LLM fallback: %s", page_url, exc)
        yield {"type": "PROGRESS", "message": f"TinyFish 不可用（{exc}），改用本地 LLM…"}
        yield from stream_extraction_via_llm(page_url, page_title)


# ──────────────────────────────────────────────────────────────
# Stage 3: Mapping — convert extracted roles to Clawcross team
# ──────────────────────────────────────────────────────────────

@dataclass
class ExtractedRole:
    """A role extracted from web crawl data or selected from expert pool."""
    role_name: str
    personality_traits: list[str] = field(default_factory=list)
    primary_responsibilities: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)  # a.k.a. input_dependency
    tools_used: list[str] = field(default_factory=list)
    source_url: str = ""
    expert_tag: str = ""  # If set, directly use preset persona from PRESET_POOL
    output_target: list[str] = field(default_factory=list)  # downstream roles


def parse_extracted_roles(extraction_results: list[dict]) -> list[ExtractedRole]:
    """Parse extraction results into ExtractedRole objects."""
    roles: list[ExtractedRole] = []
    seen_names: set[str] = set()

    for result in extraction_results:
        if result.get("status") != "COMPLETED":
            continue

        raw = result.get("result")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue

        if not isinstance(raw, dict):
            continue

        # Try to find roles array
        role_list = raw.get("roles") or raw.get("data") or raw.get("results") or []
        if isinstance(raw, dict) and not role_list:
            # Maybe the result is nested
            for val in raw.values():
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    role_list = val
                    break

        for item in role_list:
            if not isinstance(item, dict):
                continue
            name = str(item.get("role_name", "")).strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            # Merge input_dependency (new) + depends_on (legacy) for backward compat
            deps = item.get("input_dependency") or item.get("depends_on") or []
            roles.append(ExtractedRole(
                role_name=name,
                personality_traits=item.get("personality_traits") or [],
                primary_responsibilities=item.get("primary_responsibilities") or [],
                depends_on=deps,
                tools_used=item.get("tools_used") or [],
                source_url=result.get("url", ""),
                output_target=item.get("output_target") or [],
            ))

    return roles


def serialize_extracted_role(role: ExtractedRole) -> dict[str, Any]:
    return {
        "role_name": role.role_name,
        "personality_traits": list(role.personality_traits),
        "primary_responsibilities": list(role.primary_responsibilities),
        "depends_on": list(role.depends_on),
        "tools_used": list(role.tools_used),
        "source_url": role.source_url,
        "expert_tag": role.expert_tag,
        "output_target": list(role.output_target),
    }


def serialize_extracted_roles(roles: list[ExtractedRole]) -> list[dict[str, Any]]:
    return [serialize_extracted_role(role) for role in roles]


TEAM_CREATOR_TASK_TO_ROLES_PROMPT = (
    "From the user's team/task description, propose 4-10 roles for a multi-agent workflow.\n\n"
    "Description:\n{task_description}\n\n"
    "Output JSON MUST use the same role shape as ClawCross Creator page extraction:\n"
    "role_name, personality_traits[], primary_responsibilities[], input_dependency[], "
    "output_target[], tools_used[].\n"
    "Use role_name strings consistently in input_dependency and output_target.\n\n"
    "Return ONLY valid JSON, no markdown:\n"
    '{{"roles": [{{"role_name": "", "personality_traits": [], "primary_responsibilities": [], '
    '"input_dependency": [], "output_target": [], "tools_used": []}}]}}'
)


def _truncate_for_llm_error(text: str, max_len: int = 1800) -> str:
    t = (text or "").replace("\r\n", "\n").strip()
    if not t:
        return "(空输出)"
    if len(t) <= max_len:
        return t
    half = max_len // 2
    return t[:half] + "\n…\n…(中间已省略)\n…\n" + t[-half:]


def _preprocess_llm_json_blob(raw: str) -> str:
    """Strip noise so json.loads is more likely to succeed."""
    s = (raw or "").strip()
    if not s:
        return s
    if s.startswith("\ufeff"):
        s = s.lstrip("\ufeff")
    m = re.search(r"```(?:json|JSON)?\s*([\s\S]*?)```", s)
    if m:
        s = m.group(1).strip()
    lbrace, lbrack = s.find("{"), s.find("[")
    candidates = [i for i in (lbrace, lbrack) if i >= 0]
    if candidates:
        start = min(candidates)
        if start > 0:
            s = s[start:]
    return s.strip()


def _balanced_json_slice(text: str, open_ch: str, close_ch: str) -> str | None:
    i = text.find(open_ch)
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    quote = ""
    for j in range(i, len(text)):
        c = text[j]
        if esc:
            esc = False
            continue
        if in_str:
            if c == "\\":
                esc = True
            elif c == quote:
                in_str = False
            continue
        if c in "\"'":
            in_str = True
            quote = c
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[i : j + 1]
    return None


def _coerce_root_to_roles_dict(parsed: Any) -> dict | None:
    """Normalize LLM root shape to {\"roles\": [...]} for parse_extracted_roles."""
    if isinstance(parsed, dict):
        if isinstance(parsed.get("roles"), list):
            return parsed
        for key in ("data", "results", "team_roles", "agents"):
            v = parsed.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return {"roles": v}
        if "role_name" in parsed and isinstance(parsed.get("role_name"), str):
            return {"roles": [parsed]}
    if isinstance(parsed, list) and parsed and all(isinstance(x, dict) for x in parsed):
        return {"roles": parsed}
    return None


def _parse_roles_payload_from_llm_text(raw_text: str) -> dict | None:
    """Parse model output into {\"roles\": [...]} or return None."""
    clean = _preprocess_llm_json_blob(raw_text)
    if not clean:
        return None

    try:
        parsed = json.loads(clean)
        coerced = _coerce_root_to_roles_dict(parsed)
        if coerced is not None:
            return coerced
    except json.JSONDecodeError:
        pass

    for slice_fn in (
        lambda t: _balanced_json_slice(t, "{", "}"),
        lambda t: _balanced_json_slice(t, "[", "]"),
    ):
        chunk = slice_fn(clean)
        if not chunk:
            continue
        try:
            parsed = json.loads(chunk)
            coerced = _coerce_root_to_roles_dict(parsed)
            if coerced is not None:
                return coerced
        except json.JSONDecodeError:
            continue

    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", clean)
    if match:
        try:
            parsed = json.loads(match.group(1))
            return _coerce_root_to_roles_dict(parsed)
        except json.JSONDecodeError:
            pass
    return None


def generate_roles_from_task_via_llm(task_description: str) -> list[dict[str, Any]]:
    """No-TinyFish discovery: task text → serialize_extracted_roles-compatible list."""
    from services.llm_factory import create_chat_model

    llm = create_chat_model(temperature=0.35, max_tokens=4096, timeout=120)
    prompt = TEAM_CREATOR_TASK_TO_ROLES_PROMPT.format(task_description=task_description)
    response = llm.invoke(prompt)
    raw_text = _llm_content_to_text(response.content if hasattr(response, "content") else str(response))
    try:
        _log.debug("generate_roles_from_task raw (%s chars): %s", len(raw_text), raw_text[:4000])
    except Exception:
        pass
    raw_payload = _parse_roles_payload_from_llm_text(raw_text)
    if raw_payload is None:
        _emit_full_llm_output_on_failure("generate_roles_from_task_via_llm.parse_failed", raw_text)
        preview = _truncate_for_llm_error(raw_text, 2000)
        raise ValueError(
            "无法把模型输出解析成含 roles 的 JSON（已尝试去掉 markdown 代码块、从首段 { } / [ ] 截取）。\n"
            f"完整输出已打印到服务终端/stderr；以下为预览：\n{preview}"
        )
    synthetic = [{"status": "COMPLETED", "result": raw_payload, "url": ""}]
    parsed = parse_extracted_roles(synthetic)
    ser = serialize_extracted_roles(parsed)
    if not ser:
        _emit_full_llm_output_on_failure("generate_roles_from_task_via_llm.zero_roles_after_parse", raw_text)
        preview = _truncate_for_llm_error(raw_text, 1200)
        raise ValueError(
            "JSON 已解析但未得到任何有效角色（缺 role_name 等）。请检查字段是否与示例一致。\n"
            f"解析到的 roles 长度: {len(raw_payload.get('roles') or [])}。完整输出已打印到服务终端/stderr；预览：\n{preview}"
        )
    return ser


def extract_roles_via_llm(page_url: str, page_title: str = "") -> dict[str, Any]:
    """TinyFish-offline extraction: same JSON shape as EXTRACTION_GOAL_TEMPLATE / frontend parse."""
    from services.llm_factory import create_chat_model

    title = (page_title or "").strip() or page_url
    prompt = EXTRACTION_LLM_PAGE_PREAMBLE.format(page_url=page_url, page_title=title) + EXTRACTION_GOAL_TEMPLATE
    llm = create_chat_model(temperature=0.2, max_tokens=4096, timeout=120)
    response = llm.invoke(prompt)
    raw_text = _llm_content_to_text(response.content if hasattr(response, "content") else str(response))
    raw_payload = _parse_roles_payload_from_llm_text(raw_text)
    if raw_payload is None:
        _emit_full_llm_output_on_failure("extract_roles_via_llm.parse_failed", raw_text)
        preview = _truncate_for_llm_error(raw_text, 2000)
        raise ValueError(
            "无法解析为含 roles 的 JSON。完整输出已打印到服务终端/stderr；预览：\n" + preview
        )

    synthetic = [{"status": "COMPLETED", "result": raw_payload, "url": page_url}]
    parsed = parse_extracted_roles(synthetic)
    return {"roles": serialize_extracted_roles(parsed)}


def _run_extraction_batch_llm(targets: list[Target]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for i, t in enumerate(targets):
        title_guess = t.name[9:].strip() if t.name.startswith("Extract: ") else t.name
        try:
            payload = extract_roles_via_llm(t.url, title_guess)
            results.append({
                "run_id": f"llm-extract-{i}",
                "target": t.name,
                "url": t.url,
                "status": "COMPLETED",
                "result": payload,
                "error": None,
            })
        except Exception as e:
            results.append({
                "run_id": f"llm-extract-{i}",
                "target": t.name,
                "url": t.url,
                "status": "FAILED",
                "result": None,
                "error": str(e),
            })
    return results


def stream_extraction_via_llm(page_url: str, page_title: str = "") -> Iterator[dict[str, Any]]:
    yield {
        "type": "PROGRESS",
        "message": "使用本地 LLM 提取（无 TINYFISH_API_KEY；仅根据 URL/标题推断）…",
    }
    try:
        payload = extract_roles_via_llm(page_url, page_title)
        yield {
            "type": "COMPLETE",
            "message": "LLM 提取完成",
            "result": json.dumps(payload, ensure_ascii=False),
        }
    except Exception as exc:
        yield {"type": "ERROR", "error": str(exc)}


def _slugify(text: str) -> str:
    """Convert text to a safe tag slug."""
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", text.lower()).strip("_")
    return slug[:32] or "role"


SMART_SELECT_PROMPT = (
    "You are a team architect. Given a list of discovered roles and a task description, "
    "select the most important roles for building an effective team.\n\n"
    "Task: {task_description}\n\n"
    "Discovered roles:\n{roles_summary}\n\n"
    "Available preset experts (these are high-quality pre-built agent personas):\n{preset_summary}\n\n"
    "Instructions:\n"
    "1. Select the top {max_roles} most important roles for this task. "
    "Prioritize roles that are essential for the task's success. "
    "Avoid redundant or overlapping roles.\n"
    "2. For each selected role, check if it semantically overlaps with any preset expert. "
    "A match means the role's responsibilities are essentially the same as the preset expert. "
    "If a role matches a preset, the system will use the preset's rich persona instead of generating a new one.\n\n"
    "Return ONLY valid JSON, no markdown:\n"
    '{{"selected_indices": [0, 2, 5], '
    '"preset_matches": [{{"role_index": 0, "matched_preset_tag": "data_analyst", '
    '"matched_preset_name": "数据分析师", "confidence": 0.85, "reason": "..."}}], '
    '"reasoning": "Brief explanation of selection strategy"}}'
)


def smart_select_roles(
    roles_json: list[dict],
    max_roles: int = 8,
    task_description: str = "",
) -> dict:
    """Use LLM to intelligently select the best N roles and match against preset experts.

    Returns:
        {
            "selected_indices": [0, 2, 5, ...],
            "preset_matches": [
                {"role_index": 0, "matched_preset_tag": "...", "matched_preset_name": "...",
                 "confidence": 0.85, "reason": "..."},
            ],
            "reasoning": "..."
        }
    """
    from services.llm_factory import create_chat_model

    if not roles_json:
        return {"selected_indices": [], "preset_matches": [], "reasoning": "No roles provided"}

    # Cap max_roles
    max_roles = min(max(1, max_roles), 30)
    if len(roles_json) <= max_roles:
        # All roles fit — still run preset matching
        selected_indices = list(range(len(roles_json)))
    else:
        selected_indices = None  # Let LLM decide

    # Build roles summary
    role_lines = []
    for i, role in enumerate(roles_json):
        name = role.get("role_name", "")
        traits = ", ".join(role.get("personality_traits", [])[:3])
        resps = ", ".join(role.get("primary_responsibilities", [])[:3])
        deps = ", ".join(role.get("depends_on", [])[:3])
        role_lines.append(f"  [{i}] {name}: traits=[{traits}], responsibilities=[{resps}], depends_on=[{deps}]")
    roles_summary = "\n".join(role_lines)

    # Build preset summary (top relevant ones, don't overwhelm the prompt)
    preset_lines = []
    for tag, p in list(PRESET_POOL.items())[:50]:
        desc = p.get("description", "") or (p.get("persona", "") or "")[:80]
        preset_lines.append(f"  - {tag}: {p.get('name', tag)} — {desc}")
    preset_summary = "\n".join(preset_lines) if preset_lines else "(no presets available)"

    prompt = SMART_SELECT_PROMPT.format(
        task_description=task_description or "General team building",
        roles_summary=roles_summary,
        max_roles=max_roles,
        preset_summary=preset_summary,
    )

    try:
        llm = create_chat_model(temperature=0.2, max_tokens=2048, timeout=60)
        response = llm.invoke(prompt)
        raw_content = response.content if hasattr(response, "content") else str(response)

        # Handle OpenAI Responses API format
        if isinstance(raw_content, list):
            text_parts = []
            for block in raw_content:
                if isinstance(block, dict) and block.get("text"):
                    text_parts.append(block["text"])
                elif isinstance(block, str):
                    text_parts.append(block)
            raw_text = "\n".join(text_parts)
        else:
            raw_text = str(raw_content)

        _log.info("Smart select LLM raw (first 500): %s", raw_text[:500])

        # Parse JSON
        clean = raw_text.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```\w*\n?", "", clean)
            clean = re.sub(r"\n?```$", "", clean)
            clean = clean.strip()

        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", clean, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                _log.warning("Smart select: could not parse JSON")
                # Fallback: select first max_roles, no preset matches
                return {
                    "selected_indices": list(range(min(len(roles_json), max_roles))),
                    "preset_matches": [],
                    "reasoning": "LLM response parsing failed, using first N roles",
                }

        result_indices = data.get("selected_indices", [])
        result_matches = data.get("preset_matches", [])
        reasoning = data.get("reasoning", "")

        # Validate indices
        valid_indices = [
            idx for idx in result_indices
            if isinstance(idx, int) and 0 <= idx < len(roles_json)
        ]

        # Validate preset matches — ensure matched tags exist in PRESET_POOL
        valid_matches = []
        for m in result_matches:
            if not isinstance(m, dict):
                continue
            idx = m.get("role_index")
            tag = m.get("matched_preset_tag", "")
            if isinstance(idx, int) and 0 <= idx < len(roles_json) and tag in PRESET_POOL:
                preset = PRESET_POOL[tag]
                valid_matches.append({
                    "role_index": idx,
                    "matched_preset_tag": tag,
                    "matched_preset_name": preset.get("name", tag),
                    "confidence": float(m.get("confidence", 0.7)),
                    "reason": m.get("reason", ""),
                })

        return {
            "selected_indices": valid_indices[:max_roles],
            "preset_matches": valid_matches,
            "reasoning": reasoning,
        }

    except Exception as exc:
        _log.warning("Smart select LLM failed: %s", exc, exc_info=True)
        # Fallback
        return {
            "selected_indices": list(range(min(len(roles_json), max_roles))),
            "preset_matches": [],
            "reasoning": f"LLM call failed ({exc}), using first N roles",
        }


def _match_preset(role: ExtractedRole) -> dict | None:
    """Try to match an extracted role to a preset expert from the full 70+ expert pool.

    Uses multi-field matching across tag, name, name_zh, name_en, category,
    and description — much more capable than the old 10-keyword-group approach.
    """
    # Build search text from role data
    name_lower = role.role_name.lower()
    traits_text = " ".join(role.personality_traits).lower()
    responsibilities_text = " ".join(role.primary_responsibilities).lower()
    tools_text = " ".join(role.tools_used).lower()
    combined = f"{name_lower} {traits_text} {responsibilities_text} {tools_text}"

    best_tag: str | None = None
    best_score = 0

    for tag, preset in PRESET_POOL.items():
        score = 0

        # Build preset's searchable text
        preset_name = preset.get("name", "").lower()
        preset_name_zh = preset.get("name_zh", "").lower()
        preset_name_en = preset.get("name_en", "").lower()
        preset_tag = tag.lower().replace("_", " ")
        preset_cat = preset.get("category", "").lower().replace("-", " ")
        preset_desc = preset.get("description", "").lower()

        # 1. Exact name match (highest value — user picked this expert by name)
        if name_lower == preset_name or name_lower == preset_name_zh or name_lower == preset_name_en:
            score += 10
        # Partial name containment
        elif preset_name and preset_name in combined:
            score += 4
        elif preset_name_zh and preset_name_zh in combined:
            score += 4
        elif preset_name_en and preset_name_en in combined:
            score += 3

        # 2. Tag match — e.g. "data" in "数据分析师 数据驱动 数据建模"
        tag_words = preset_tag.split()
        for tw in tag_words:
            if len(tw) >= 2 and tw in combined:
                score += 3

        # 3. Category match — e.g. "engineering" in role about backend dev
        if preset_cat:
            cat_words = preset_cat.split()
            for cw in cat_words:
                if len(cw) >= 3 and cw in combined:
                    score += 2

        # 4. Description keyword overlap — significant terms from description
        if preset_desc:
            # Extract significant words (length >= 3 to skip stop words)
            desc_words = set(
                w for w in re.split(r"[\s,，.。;；!！?？()\[\]]+", preset_desc)
                if len(w) >= 3
            )
            matches = sum(1 for w in desc_words if w in combined)
            score += min(matches, 4)  # Cap at 4 to avoid overwhelming

        # 5. Role name contains preset name words (reverse check)
        if preset_name_zh:
            for char_seq in [preset_name_zh[i:i+2] for i in range(len(preset_name_zh) - 1)]:
                if char_seq in name_lower:
                    score += 2

        if score > best_score:
            best_score = score
            best_tag = tag

    # Require minimum confidence (lowered from old threshold since we have richer matching)
    if best_tag and best_score >= 3:
        return PRESET_POOL[best_tag]
    return None


def _build_persona(role: ExtractedRole) -> str:
    """Build a persona prompt from extracted role data."""
    parts = [f"你是{role.role_name}。"]

    if role.personality_traits:
        traits = "、".join(role.personality_traits[:5])
        parts.append(f"你的核心特质包括：{traits}。")

    if role.primary_responsibilities:
        resp_list = "\n".join(f"- {r}" for r in role.primary_responsibilities[:5])
        parts.append(f"你的主要职责：\n{resp_list}")

    if role.tools_used:
        tools = "、".join(role.tools_used[:5])
        parts.append(f"你擅长使用的工具：{tools}。")

    parts.append("你需要在团队中发挥专业价值，围绕自己的职责给出具体、可执行的建议。")
    return "\n\n".join(parts)


# ──────────────────────────────────────────────────────────────
# Stage 3b: Workflow Graph Enhancement — ClawCross Studio graph mode
# ──────────────────────────────────────────────────────────────

import logging as _logging

_log = _logging.getLogger(__name__)


def _emit_full_llm_output_on_failure(context: str, raw_text: str) -> None:
    """On parse/validation failure: print full model text to stderr and emit ERROR log."""
    import sys

    text = raw_text if raw_text is not None else ""
    header = f"[ClawCross Creator] {context} — full LLM output ({len(text)} chars)\n"
    try:
        print(header + text, file=sys.stderr, flush=True)
    except Exception:
        pass
    try:
        _log.error("%s — full LLM output (%d chars):\n%s", context, len(text), text)
    except Exception:
        pass


_REVIEW_KEYWORDS = (
    "review", "reviewer", "qa", "quality", "approve", "approval", "audit", "compliance",
    "gate", "signoff", "sign-off", "editor", "validate", "validation", "test", "testing",
    "审核", "评审", "审批", "质量", "质检", "测试", "验证", "合规", "把关", "校验",
)
_LEAD_KEYWORDS = (
    "lead", "manager", "head", "director", "owner", "architect", "planner", "coordinator",
    "synthesis", "strategist", "负责人", "经理", "总监", "架构", "规划", "统筹", "协调", "总结", "汇总",
)
_EXECUTION_KEYWORDS = (
    "engineer", "developer", "designer", "writer", "operator", "ops", "specialist",
    "implementation", "implement", "build", "delivery", "开发", "设计", "执行", "实现", "交付", "运营",
)
_SUPPORTED_CONDITION_PREFIXES = (
    "last_post_contains:",
    "last_post_not_contains:",
    "post_count_gte:",
    "post_count_lt:",
)

WORKFLOW_GRAPH_PROMPT = (
    "You are designing a Clawcross / OASIS graph-mode workflow for a multi-agent team.\n\n"
    "Task context: {task_description}\n\n"
    "Roles:\n{roles_summary}\n\n"
    "Available graph features:\n"
    "- expert nodes (one role performs a step)\n"
    "- manual boundary nodes with author=begin / author=bend\n"
    "- selector nodes (selector=true) with selector_edges for routing / review gates\n"
    "- conditional_edges only when a concrete pass/fail condition is explicit\n\n"
    "Design a workflow that reflects the real collaboration architecture instead of a generic linear chain.\n"
    "Use parallel branches, fan-in merges, review gates, and rework loops when the role set supports them.\n\n"
    "Rules:\n"
    "- Every expert node must use an exact role name from the list\n"
    "- Include both a begin boundary node and a bend boundary node\n"
    "- Every role must appear at least once\n"
    "- Selector node outgoing routes must go in selector_edges, not regular edges\n"
    "- Keep the workflow compact and high-signal\n"
    "- Use ids in snake_case\n\n"
    "Return ONLY valid JSON:\n"
    '{{'
    '"workflow_graph": {{'
    '"plan": ['
    '{{"id": "begin", "type": "manual", "author": "begin", "content": "Start"}},'
    '{{"id": "research", "type": "expert", "performing_role": "Role Name", "instruction": "What this role does"}},'
    '{{"id": "review_gate", "type": "expert", "performing_role": "Reviewer Role", "instruction": "Review and choose route", "selector": true}},'
    '{{"id": "end", "type": "manual", "author": "bend", "content": "Done"}}'
    '],'
    '"edges": [["begin", "research"], ["research", "review_gate"]],'
    '"selector_edges": [{{"source": "review_gate", "choices": {{"1": "end", "2": "research"}}}}],'
    '"conditional_edges": [{{"source": "review_gate", "condition": "last_post_contains:APPROVED", "then": "end", "else": "research"}}],'
    '"reasoning": "One short paragraph"'
    '}}'
)


def _compact_text(value: Any, default: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or default


def _llm_content_to_text(raw_content: Any) -> str:
    if isinstance(raw_content, list):
        text_parts = []
        for block in raw_content:
            if isinstance(block, dict) and block.get("text"):
                text_parts.append(block["text"])
            elif isinstance(block, str):
                text_parts.append(block)
        return "\n".join(text_parts)
    return str(raw_content)


def _extract_json_payload(raw_text: str) -> dict | None:
    clean = raw_text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```\w*\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean)
        clean = clean.strip()

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                return None
    return None


_TRANSLATION_CACHE: dict[tuple[str, str, str], str] = {}

_TRANSLATION_PROMPT = (
    "You are a bilingual UI localization engine for Clawcross ClawCross Creator.\n\n"
    "Translate each input string into {target_language}.\n"
    "Return ONLY valid JSON with this schema:\n"
    '{{"translations": ["...", "..."]}}\n\n'
    "Rules:\n"
    "- Preserve the number of items and their order exactly.\n"
    "- Keep URLs, emails, file paths, JSON/YAML syntax, markdown structure, emojis, code, IDs, and tags intact.\n"
    "- Keep product and brand names unchanged unless the input already contains an accepted localized form.\n"
    "- Clawcross, ClawCross Creator, ClawCross Studio, TinyFish, OpenClaw, OASIS should stay unchanged.\n"
    "- If a string is already in the target language or should remain as-is, return it unchanged.\n"
    "- Translate UI-facing prose naturally and concisely.\n"
    "- Do not explain anything.\n\n"
    "Context: {context}\n"
    "Input strings JSON:\n"
    "{texts_json}"
)


def translate_texts_via_llm(
    texts: list[str],
    *,
    target_lang: str,
    source_lang: str = "",
    context: str = "",
) -> list[str]:
    """Translate a batch of UI strings using the configured Clawcross LLM.

    The function is best-effort: on any failure it returns the original strings
    so the frontend can still render without breaking the workflow.
    """
    target = "en" if str(target_lang or "").lower().startswith("en") else "zh"
    target_label = "English" if target == "en" else "Simplified Chinese"
    normalized = [str(text or "") for text in texts or []]
    if not normalized:
        return []

    if len(_TRANSLATION_CACHE) > 5000:
        _TRANSLATION_CACHE.clear()

    result = list(normalized)
    missing_keys: list[tuple[str, str, str]] = []
    missing_texts: list[str] = []
    positions_by_key: dict[tuple[str, str, str], list[int]] = {}
    context_key = _compact_text(context, "general")

    for idx, text in enumerate(normalized):
        cache_key = (target, context_key, text)
        cached = _TRANSLATION_CACHE.get(cache_key)
        if cached is not None:
            result[idx] = cached
            continue
        positions_by_key.setdefault(cache_key, []).append(idx)
        if cache_key not in missing_keys:
            missing_keys.append(cache_key)
            missing_texts.append(text)

    if not missing_texts:
        return result

    try:
        from services.llm_factory import create_chat_model

        llm = create_chat_model(temperature=0.0, max_tokens=4096, timeout=90)
        prompt = _TRANSLATION_PROMPT.format(
            target_language=target_label,
            context=_compact_text(context, "general UI"),
            texts_json=_json_dumps(missing_texts),
        )
        if source_lang:
            prompt += f"\nSource language hint: {source_lang}\n"

        response = llm.invoke(prompt)
        raw_text = _llm_content_to_text(response.content if hasattr(response, "content") else str(response))
        data = _extract_json_payload(raw_text)
        translations = data.get("translations") if isinstance(data, dict) else None
        if not isinstance(translations, list) or len(translations) != len(missing_texts):
            raise ValueError("LLM translation payload shape mismatch")

        for key, translated in zip(missing_keys, translations):
            translated_text = str(translated or "")
            if not translated_text:
                translated_text = key[2]
            _TRANSLATION_CACHE[key] = translated_text
            for idx in positions_by_key.get(key, []):
                result[idx] = translated_text
        return result
    except Exception as exc:
        _log.warning("Dynamic ClawCross Creator translation failed: %s", exc)
        for key in missing_keys:
            fallback = key[2]
            _TRANSLATION_CACHE[key] = fallback
            for idx in positions_by_key.get(key, []):
                result[idx] = fallback
        return result


def _slugify_step_id(text: str, fallback: str = "step") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return slug[:48] or fallback


def _unique_step_id(base: str, used_ids: set[str], fallback: str = "step") -> str:
    root = _slugify_step_id(base, fallback)
    step_id = root
    counter = 2
    while step_id in used_ids:
        step_id = f"{root}_{counter}"
        counter += 1
    used_ids.add(step_id)
    return step_id


def _normalize_role_key(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _resolve_role_name(candidate: Any, roles: list[ExtractedRole]) -> str:
    text = str(candidate or "").strip()
    if not text:
        return ""

    exact = {role.role_name: role.role_name for role in roles}
    if text in exact:
        return exact[text]

    normalized = {_normalize_role_key(role.role_name): role.role_name for role in roles}
    key = _normalize_role_key(text)
    if key in normalized:
        return normalized[key]

    for role in roles:
        role_key = _normalize_role_key(role.role_name)
        if key and (key in role_key or role_key in key):
            return role.role_name

    return ""


def _role_text_blob(role: ExtractedRole) -> str:
    return " ".join([
        role.role_name,
        " ".join(role.personality_traits),
        " ".join(role.primary_responsibilities),
        " ".join(role.tools_used),
    ]).lower()


def _role_stage_score(role: ExtractedRole) -> int:
    text = _role_text_blob(role)
    if any(keyword in text for keyword in _REVIEW_KEYWORDS):
        return 4
    if any(keyword in text for keyword in _LEAD_KEYWORDS):
        return 3
    if any(keyword in text for keyword in _EXECUTION_KEYWORDS):
        return 2
    return 1


def _default_role_instruction(role: ExtractedRole, task_description: str = "") -> str:
    duty_text = "；".join(role.primary_responsibilities[:2]).strip()
    if role.depends_on:
        deps = "、".join(role.depends_on[:3])
        if duty_text:
            return f"吸收 {deps} 的输出后，负责：{duty_text}"
        return f"基于 {deps} 的输出推进 {role.role_name} 的关键产出"
    if duty_text:
        return duty_text
    if task_description:
        return f"围绕“{task_description[:60]}”提供 {role.role_name} 的专业输出"
    return f"完成 {role.role_name} 在团队中的核心职责"


def _review_selector_instruction(role: ExtractedRole, task_description: str = "") -> str:
    base = _default_role_instruction(role, task_description)
    return f"{base}，并判断当前产出是进入完成态还是返回上游迭代"


def _find_review_role(roles: list[ExtractedRole]) -> ExtractedRole | None:
    candidates = []
    for index, role in enumerate(roles):
        text = _role_text_blob(role)
        score = sum(1 for keyword in _REVIEW_KEYWORDS if keyword in text)
        if score:
            candidates.append((score, _role_stage_score(role), -index, role))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][3]


def _build_default_workflow_graph(
    roles: list[ExtractedRole],
    task_description: str = "",
) -> dict[str, Any]:
    """Build a deterministic ClawCross Studio-style workflow graph.

    This uses graph-mode concepts supported in ClawCross Studio:
    manual begin/end nodes, fan-out/fan-in edges, and a selector review gate
    when the role set includes an obvious QA / reviewer / approver role.
    """
    used_ids: set[str] = set()
    begin_id = _unique_step_id("begin", used_ids, "begin")
    end_id = _unique_step_id("end", used_ids, "end")

    plan: list[dict[str, Any]] = [
        {"id": begin_id, "type": "manual", "author": "begin", "content": task_description or "团队协作开始"},
    ]

    role_step_ids: dict[str, str] = {}
    role_order: dict[str, int] = {}
    for idx, role in enumerate(roles):
        step_id = _unique_step_id(role.role_name, used_ids, f"role_{idx + 1}")
        role_step_ids[role.role_name] = step_id
        role_order[role.role_name] = idx
        plan.append({
            "id": step_id,
            "type": "expert",
            "performing_role": role.role_name,
            "instruction": _default_role_instruction(role, task_description),
        })

    plan.append({
        "id": end_id,
        "type": "manual",
        "author": "bend",
        "content": "团队协作完成",
    })

    edges: list[list[str]] = []
    edge_seen: set[tuple[str, str]] = set()

    def add_edge(source: str, target: str) -> None:
        pair = (source, target)
        if source and target and source != target and pair not in edge_seen:
            edge_seen.add(pair)
            edges.append([source, target])

    explicit_dependencies = False
    for role in roles:
        target_id = role_step_ids[role.role_name]
        valid_deps = [dep for dep in role.depends_on if dep in role_step_ids and dep != role.role_name]
        if valid_deps:
            explicit_dependencies = True
            for dep_name in valid_deps:
                add_edge(role_step_ids[dep_name], target_id)

    if explicit_dependencies:
        incoming = {sid: 0 for sid in role_step_ids.values()}
        for source, target in edges:
            incoming[target] = incoming.get(target, 0) + 1
        for role in roles:
            step_id = role_step_ids[role.role_name]
            if incoming.get(step_id, 0) == 0:
                add_edge(begin_id, step_id)
    elif len(roles) == 1:
        add_edge(begin_id, role_step_ids[roles[0].role_name])
    elif roles:
        grouped_roles: list[tuple[int, list[str]]] = []
        for role in sorted(roles, key=lambda item: (_role_stage_score(item), role_order[item.role_name])):
            score = _role_stage_score(role)
            if not grouped_roles or grouped_roles[-1][0] != score:
                grouped_roles.append((score, [role.role_name]))
            else:
                grouped_roles[-1][1].append(role.role_name)

        first_group = grouped_roles[0][1]
        for role_name in first_group:
            add_edge(begin_id, role_step_ids[role_name])

        for group_idx in range(1, len(grouped_roles)):
            prev_group = grouped_roles[group_idx - 1][1]
            current_group = grouped_roles[group_idx][1]
            for current_role in current_group:
                for prev_role in prev_group:
                    add_edge(role_step_ids[prev_role], role_step_ids[current_role])

    selector_edges: list[dict[str, Any]] = []
    review_role = _find_review_role(roles)
    if review_role:
        review_id = role_step_ids[review_role.role_name]
        incoming_to_review = [source for source, target in edges if target == review_id and source != begin_id]

        if not incoming_to_review:
            outgoing = {sid: 0 for sid in role_step_ids.values()}
            for source, target in edges:
                if source in outgoing and target in outgoing:
                    outgoing[source] += 1
            terminal_candidates = [
                role_step_ids[role.role_name]
                for role in roles
                if role.role_name != review_role.role_name and outgoing.get(role_step_ids[role.role_name], 0) == 0
            ]
            for source_id in terminal_candidates:
                add_edge(source_id, review_id)
            incoming_to_review = [source for source, target in edges if target == review_id and source != begin_id]

        if incoming_to_review:
            for step in plan:
                if step.get("id") == review_id:
                    step["selector"] = True
                    step["instruction"] = _review_selector_instruction(review_role, task_description)
                    break

            rework_target = incoming_to_review[-1]
            selector_edges.append({
                "source": review_id,
                "choices": {"1": end_id, "2": rework_target},
            })

    outgoing_any = {sid: 0 for sid in role_step_ids.values()}
    for source, target in edges:
        if source in outgoing_any and target != end_id:
            outgoing_any[source] += 1
    for selector_edge in selector_edges:
        outgoing_any[selector_edge["source"]] = outgoing_any.get(selector_edge["source"], 0) + len(selector_edge.get("choices", {}))

    for role in roles:
        step_id = role_step_ids[role.role_name]
        if outgoing_any.get(step_id, 0) == 0:
            add_edge(step_id, end_id)

    if not roles:
        add_edge(begin_id, end_id)

    review_loops = sum(
        1
        for entry in selector_edges
        if any(target != end_id for target in entry.get("choices", {}).values())
    )

    return {
        "version": 2,
        "repeat": False,
        "plan": plan,
        "edges": edges,
        "selector_edges": selector_edges,
        "conditional_edges": [],
        "meta": {
            "mode": "heuristic",
            "llm_enhanced": False,
            "expert_nodes": sum(1 for step in plan if step.get("type") == "expert"),
            "manual_nodes": sum(1 for step in plan if step.get("type") == "manual"),
            "selector_nodes": len(selector_edges),
            "conditional_edges": 0,
            "review_loops": review_loops,
        },
    }


def _normalize_workflow_graph(
    graph: dict[str, Any],
    roles: list[ExtractedRole],
) -> dict[str, Any] | None:
    if not isinstance(graph, dict):
        return None

    raw_plan = graph.get("plan") or graph.get("nodes") or []
    if not isinstance(raw_plan, list):
        return None

    used_ids: set[str] = set()
    plan: list[dict[str, Any]] = []
    valid_ids: set[str] = set()

    for idx, item in enumerate(raw_plan):
        if not isinstance(item, dict):
            continue

        item_type = str(item.get("type") or "").strip().lower()
        if "manual" in item:
            item_type = "manual"
        elif "expert" in item and item_type != "manual":
            item_type = "expert"

        if item_type == "manual" or item.get("author"):
            manual = item.get("manual") if isinstance(item.get("manual"), dict) else {}
            author = str(
                manual.get("author")
                or item.get("author")
                or "主持人"
            ).strip()
            author_key = author.lower()
            if author_key in {"begin", "bstart", "start"}:
                author = "begin"
            elif author_key in {"bend", "end", "finish"}:
                author = "bend"

            step_id = _unique_step_id(item.get("id") or author or f"manual_{idx + 1}", used_ids, f"manual_{idx + 1}")
            plan.append({
                "id": step_id,
                "type": "manual",
                "author": author,
                "content": _compact_text(manual.get("content") or item.get("content"), ""),
            })
            valid_ids.add(step_id)
            continue

        role_name = _resolve_role_name(
            item.get("performing_role") or item.get("role") or item.get("expert_role") or item.get("expert"),
            roles,
        )
        if not role_name:
            continue

        step_id = _unique_step_id(item.get("id") or role_name or f"step_{idx + 1}", used_ids, f"step_{idx + 1}")
        plan.append({
            "id": step_id,
            "type": "expert",
            "performing_role": role_name,
            "instruction": _compact_text(item.get("instruction") or item.get("content"), f"推进 {role_name} 阶段任务"),
            "selector": bool(item.get("selector") or item.get("is_selector")),
        })
        valid_ids.add(step_id)

    if not plan:
        return None

    def normalize_fixed_edges(raw_edges: Any) -> list[list[str]]:
        fixed_edges: list[list[str]] = []
        seen: set[tuple[str, str]] = set()
        if not isinstance(raw_edges, list):
            return fixed_edges
        for edge in raw_edges:
            source = target = ""
            if isinstance(edge, (list, tuple)) and len(edge) >= 2:
                source = str(edge[0]).strip()
                target = str(edge[1]).strip()
            elif isinstance(edge, dict):
                source = str(edge.get("source") or edge.get("from") or "").strip()
                target = str(edge.get("target") or edge.get("to") or "").strip()
            pair = (source, target)
            if source in valid_ids and target in valid_ids and source != target and pair not in seen:
                seen.add(pair)
                fixed_edges.append([source, target])
        return fixed_edges

    def normalize_selector_edges(raw_selector_edges: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if not isinstance(raw_selector_edges, list):
            return normalized
        for entry in raw_selector_edges:
            if not isinstance(entry, dict):
                continue
            source = str(entry.get("source") or "").strip()
            if source not in valid_ids:
                continue
            raw_choices = entry.get("choices")
            if not isinstance(raw_choices, dict):
                continue

            mapped_choices: dict[str, str] = {}
            for index, key in enumerate(sorted(raw_choices.keys(), key=lambda item: (not str(item).isdigit(), str(item))), start=1):
                target = str(raw_choices[key]).strip()
                if target in valid_ids and target != source:
                    choice_key = str(key) if str(key).isdigit() else str(index)
                    mapped_choices[choice_key] = target

            if mapped_choices:
                normalized.append({"source": source, "choices": mapped_choices})

        return normalized

    def normalize_conditional_edges(raw_conditional_edges: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        if not isinstance(raw_conditional_edges, list):
            return normalized
        for entry in raw_conditional_edges:
            if not isinstance(entry, dict):
                continue
            source = str(entry.get("source") or "").strip()
            then_target = str(entry.get("then") or "").strip()
            else_target = str(entry.get("else") or "").strip()
            condition = _compact_text(entry.get("condition"), "")
            if not source or source not in valid_ids or then_target not in valid_ids:
                continue
            if condition != "always" and not condition.startswith("!") and not any(
                condition.startswith(prefix) for prefix in _SUPPORTED_CONDITION_PREFIXES
            ):
                condition = ""
            if not condition:
                continue
            normalized.append({
                "source": source,
                "condition": condition,
                "then": then_target,
                "else": else_target if else_target in valid_ids else "",
            })
        return normalized

    return {
        "version": 2,
        "repeat": False,
        "plan": plan,
        "edges": normalize_fixed_edges(graph.get("edges") or []),
        "selector_edges": normalize_selector_edges(graph.get("selector_edges") or []),
        "conditional_edges": normalize_conditional_edges(graph.get("conditional_edges") or []),
        "meta": {
            "mode": str((graph.get("meta") or {}).get("mode") or "llm").strip() or "llm",
            "llm_enhanced": True,
            "reasoning": _compact_text(graph.get("reasoning") or (graph.get("meta") or {}).get("reasoning"), ""),
        },
    }


def _repair_workflow_graph(
    graph: dict[str, Any] | None,
    roles: list[ExtractedRole],
    task_description: str = "",
) -> dict[str, Any]:
    fallback = _build_default_workflow_graph(roles, task_description)
    if not graph:
        return fallback

    plan = [dict(step) for step in graph.get("plan") or [] if isinstance(step, dict)]
    if not plan:
        return fallback

    used_ids = {str(step.get("id")).strip() for step in plan if str(step.get("id", "")).strip()}
    fallback_plan = [dict(step) for step in fallback["plan"]]

    def copy_boundary(author: str) -> dict[str, Any]:
        return next(dict(step) for step in fallback_plan if step.get("type") == "manual" and step.get("author") == author)

    begin_step = next((step for step in plan if step.get("type") == "manual" and step.get("author") == "begin"), None)
    if not begin_step:
        begin_step = copy_boundary("begin")
        begin_step["id"] = _unique_step_id(begin_step["id"], used_ids, "begin")
        plan.insert(0, begin_step)

    end_step = next((step for step in plan if step.get("type") == "manual" and step.get("author") == "bend"), None)
    if not end_step:
        end_step = copy_boundary("bend")
        end_step["id"] = _unique_step_id(end_step["id"], used_ids, "end")
        plan.append(end_step)

    role_to_step_ids: dict[str, list[str]] = {}
    for step in plan:
        role_name = step.get("performing_role")
        if role_name:
            role_to_step_ids.setdefault(role_name, []).append(step["id"])

    for fallback_step in fallback_plan:
        role_name = fallback_step.get("performing_role")
        if not role_name or role_name in role_to_step_ids:
            continue
        cloned = dict(fallback_step)
        cloned["id"] = _unique_step_id(cloned["id"], used_ids, "role")
        role_to_step_ids.setdefault(role_name, []).append(cloned["id"])
        plan.insert(-1, cloned)

    begin_id = begin_step["id"]
    end_id = end_step["id"]
    valid_ids = {step["id"] for step in plan}
    fallback_by_id = {step["id"]: step for step in fallback_plan}

    def map_fallback_step_id(fallback_step_id: str) -> str:
        step = fallback_by_id.get(fallback_step_id, {})
        if step.get("type") == "manual":
            if step.get("author") == "begin":
                return begin_id
            if step.get("author") == "bend":
                return end_id
            return ""
        role_name = step.get("performing_role")
        if role_name and role_to_step_ids.get(role_name):
            return role_to_step_ids[role_name][0]
        return ""

    def normalize_edges(raw_edges: Any) -> list[list[str]]:
        normalized: list[list[str]] = []
        seen: set[tuple[str, str]] = set()
        for source, target in (raw_edges or []):
            pair = (source, target)
            if source in valid_ids and target in valid_ids and source != target and pair not in seen:
                seen.add(pair)
                normalized.append([source, target])
        return normalized

    edges = normalize_edges(graph.get("edges") or [])
    selector_edges = []
    selector_sources: set[str] = set()
    for entry in graph.get("selector_edges") or []:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "").strip()
        raw_choices = entry.get("choices") or {}
        if source not in valid_ids or not isinstance(raw_choices, dict):
            continue
        mapped = {}
        for key in sorted(raw_choices.keys(), key=lambda item: (not str(item).isdigit(), str(item))):
            target = str(raw_choices[key]).strip()
            if target in valid_ids and target != source:
                mapped[str(key)] = target
        if mapped:
            selector_sources.add(source)
            selector_edges.append({"source": source, "choices": mapped})

    conditional_edges = []
    for entry in graph.get("conditional_edges") or []:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "").strip()
        then_target = str(entry.get("then") or "").strip()
        else_target = str(entry.get("else") or "").strip()
        condition = _compact_text(entry.get("condition"), "")
        if source in valid_ids and then_target in valid_ids and condition:
            conditional_edges.append({
                "source": source,
                "condition": condition,
                "then": then_target,
                "else": else_target if else_target in valid_ids else "",
            })

    for step in plan:
        if step["id"] in selector_sources:
            step["selector"] = True

    # Selector nodes must route via selector_edges / conditional_edges only.
    edges = [edge for edge in edges if edge[0] not in selector_sources]

    if not edges and not selector_edges and not conditional_edges:
        translated_edges = []
        translated_selector_edges = []
        translated_conditional_edges = []

        for source, target in fallback["edges"]:
            mapped_source = map_fallback_step_id(source)
            mapped_target = map_fallback_step_id(target)
            if mapped_source and mapped_target and mapped_source != mapped_target:
                translated_edges.append([mapped_source, mapped_target])

        for entry in fallback["selector_edges"]:
            mapped_source = map_fallback_step_id(entry.get("source", ""))
            if not mapped_source:
                continue
            mapped_choices = {}
            for key, target in (entry.get("choices") or {}).items():
                mapped_target = map_fallback_step_id(target)
                if mapped_target and mapped_target != mapped_source:
                    mapped_choices[str(key)] = mapped_target
            if mapped_choices:
                translated_selector_edges.append({"source": mapped_source, "choices": mapped_choices})

        for entry in fallback["conditional_edges"]:
            mapped_source = map_fallback_step_id(entry.get("source", ""))
            mapped_then = map_fallback_step_id(entry.get("then", ""))
            mapped_else = map_fallback_step_id(entry.get("else", "")) if entry.get("else") else ""
            if mapped_source and mapped_then:
                translated_conditional_edges.append({
                    "source": mapped_source,
                    "condition": entry.get("condition", ""),
                    "then": mapped_then,
                    "else": mapped_else,
                })

        edges = translated_edges
        selector_edges = translated_selector_edges
        conditional_edges = translated_conditional_edges
        selector_sources = {entry["source"] for entry in selector_edges}
        for step in plan:
            if step["id"] in selector_sources:
                step["selector"] = True

    elif not selector_edges and fallback["selector_edges"]:
        fallback_selector = fallback["selector_edges"][0]
        source_step = fallback_by_id.get(fallback_selector["source"], {})
        source_role = source_step.get("performing_role")
        if source_role and role_to_step_ids.get(source_role):
            source_id = role_to_step_ids[source_role][0]
            mapped_choices = {}
            for key, fallback_target in fallback_selector.get("choices", {}).items():
                target_step = fallback_by_id.get(fallback_target, {})
                if target_step.get("author") == "bend":
                    mapped_choices[str(key)] = end_id
                elif target_step.get("performing_role") and role_to_step_ids.get(target_step["performing_role"]):
                    mapped_choices[str(key)] = role_to_step_ids[target_step["performing_role"]][0]
            if mapped_choices:
                selector_edges.append({"source": source_id, "choices": mapped_choices})
                selector_sources.add(source_id)
                for step in plan:
                    if step["id"] == source_id:
                        step["selector"] = True
                        break
                edges = [edge for edge in edges if edge[0] != source_id]

    edge_seen = {tuple(edge) for edge in edges}

    def add_edge(source: str, target: str) -> None:
        pair = (source, target)
        if source and target and source != target and pair not in edge_seen and source not in selector_sources:
            edge_seen.add(pair)
            edges.append([source, target])

    incoming: dict[str, int] = {step_id: 0 for step_id in valid_ids}
    outgoing: dict[str, int] = {step_id: 0 for step_id in valid_ids}

    def register_connection(source: str, target: str) -> None:
        if source in outgoing:
            outgoing[source] += 1
        if target in incoming:
            incoming[target] += 1

    for source, target in edges:
        register_connection(source, target)
    for entry in conditional_edges:
        register_connection(entry["source"], entry["then"])
        if entry.get("else"):
            register_connection(entry["source"], entry["else"])
    for entry in selector_edges:
        for target in entry.get("choices", {}).values():
            register_connection(entry["source"], target)

    for step in plan:
        step_id = step["id"]
        if step_id in {begin_id, end_id}:
            continue
        if incoming.get(step_id, 0) == 0:
            add_edge(begin_id, step_id)
            register_connection(begin_id, step_id)

    for step in plan:
        step_id = step["id"]
        if step_id in {begin_id, end_id}:
            continue
        if outgoing.get(step_id, 0) == 0:
            if step_id in selector_sources:
                selector_edges.append({"source": step_id, "choices": {"1": end_id}})
                register_connection(step_id, end_id)
            else:
                add_edge(step_id, end_id)
                register_connection(step_id, end_id)

    if not any(step["id"] == begin_id for step in plan):
        plan.insert(0, begin_step)
    if not any(step["id"] == end_id for step in plan):
        plan.append(end_step)

    begin_first = [step for step in plan if step.get("type") == "manual" and step.get("author") == "begin"]
    end_last = [step for step in plan if step.get("type") == "manual" and step.get("author") == "bend"]
    middle_steps = [step for step in plan if step not in begin_first and step not in end_last]
    plan = begin_first[:1] + middle_steps + end_last[:1]

    selector_sources = {entry["source"] for entry in selector_edges}
    review_loops = sum(
        1
        for entry in selector_edges
        if any(target != end_id for target in entry.get("choices", {}).values())
    )

    meta = dict(graph.get("meta") or {})
    meta.update({
        "mode": "llm" if meta.get("llm_enhanced") else "heuristic",
        "expert_nodes": sum(1 for step in plan if step.get("type") == "expert"),
        "manual_nodes": sum(1 for step in plan if step.get("type") == "manual"),
        "selector_nodes": len(selector_sources),
        "conditional_edges": len(conditional_edges),
        "review_loops": review_loops,
    })

    return {
        "version": 2,
        "repeat": False,
        "plan": plan,
        "edges": edges,
        "selector_edges": selector_edges,
        "conditional_edges": conditional_edges,
        "meta": meta,
    }


def enhance_workflow_graph_via_llm(
    roles: list[ExtractedRole],
    task_description: str = "",
) -> dict[str, Any] | None:
    """Use the internal LLM to generate a ClawCross Studio-compatible workflow graph."""
    if len(roles) <= 1:
        return None

    lines = []
    for role in roles:
        deps = ", ".join(role.depends_on[:3]) if role.depends_on else "none"
        targets = ", ".join(role.output_target[:3]) if role.output_target else "none"
        duties = "; ".join(role.primary_responsibilities[:3]) if role.primary_responsibilities else "—"
        tools = ", ".join(role.tools_used[:3]) if role.tools_used else "—"
        lines.append(
            f"- {role.role_name}: responsibilities=[{duties}], "
            f"input_from=[{deps}], output_to=[{targets}], tools=[{tools}]"
        )

    prompt = WORKFLOW_GRAPH_PROMPT.format(
        task_description=task_description or "General team workflow",
        roles_summary="\n".join(lines),
    )

    try:
        from services.llm_factory import create_chat_model

        llm = create_chat_model(temperature=0.2, max_tokens=2600, timeout=60)
        response = llm.invoke(prompt)
        raw_text = _llm_content_to_text(response.content if hasattr(response, "content") else str(response))
        _log.info("Workflow graph LLM raw response (first 500 chars): %s", raw_text[:500])

        data = _extract_json_payload(raw_text)
        if not isinstance(data, dict):
            _log.warning("Workflow graph LLM: could not parse JSON payload")
            return None

        raw_graph = data.get("workflow_graph") or data.get("graph") or data.get("workflow") or data
        graph = _normalize_workflow_graph(raw_graph, roles)
        if not graph:
            _log.warning("Workflow graph LLM: no valid workflow graph in payload")
            return None

        graph.setdefault("meta", {})
        graph["meta"]["llm_enhanced"] = True
        graph["meta"]["mode"] = "llm"
        graph["meta"]["reasoning"] = _compact_text(
            (raw_graph.get("reasoning") if isinstance(raw_graph, dict) else "")
            or data.get("reasoning"),
            "",
        )
        return graph
    except Exception as exc:
        _log.warning("Workflow graph LLM enhancement failed: %s", exc)
        return None


def _enrich_deps_from_output_target(roles: list[ExtractedRole]) -> None:
    """Enrich depends_on using output_target (bidirectional linking).

    If role A lists role B in output_target, then B should have A in depends_on.
    This helps build a more complete dependency graph even if input_dependency is sparse.
    """
    name_to_idx = {r.role_name: i for i, r in enumerate(roles)}
    for role in roles:
        for target_name in role.output_target:
            idx = name_to_idx.get(target_name)
            if idx is not None and role.role_name not in roles[idx].depends_on:
                roles[idx].depends_on.append(role.role_name)


def map_roles_to_team(
    roles: list[ExtractedRole],
    team_name: str,
    task_description: str = "",
) -> dict[str, Any]:
    """Convert extracted roles into a complete Clawcross team configuration.

    Returns dict with:
      - oasis_experts: list of persona definitions
      - internal_agents: list of agent metadata (session generated later)
      - yaml_workflow: OASIS v2 YAML workflow string
      - summary: human-readable summary
    """
    # Enrich dependency graph from output_target fields (bidirectional linking)
    _enrich_deps_from_output_target(roles)

    experts: list[dict] = []
    agents: list[dict] = []
    used_tags: set[str] = set()

    for role in roles:
        # 1. Direct tag match — role was added from expert pool with explicit tag
        preset = None
        if role.expert_tag and role.expert_tag in PRESET_POOL:
            preset = PRESET_POOL[role.expert_tag]
        else:
            # 2. Fuzzy match — try to find the best matching preset from the full pool
            preset = _match_preset(role)

        if preset:
            tag = preset["tag"]
            if tag in used_tags:
                # Preset already used, create custom variant
                tag = _slugify(role.role_name)
                if tag in used_tags:
                    tag = f"{tag}_{len(used_tags)}"
            used_tags.add(tag)

            experts.append({
                "name": role.role_name,
                "tag": tag,
                "persona": preset["persona"] if preset["persona"] else _build_persona(role),
                "temperature": preset["temperature"],
                "matched_preset": preset["tag"],
                "source": preset["source"],
            })
        else:
            # Generate custom persona
            tag = _slugify(role.role_name)
            if tag in used_tags:
                tag = f"{tag}_{len(used_tags)}"
            used_tags.add(tag)

            experts.append({
                "name": role.role_name,
                "tag": tag,
                "persona": _build_persona(role),
                "temperature": 0.7,
                "source": "generated",
            })

        agents.append({
            "name": role.role_name,
            "tag": tag,
        })

    workflow_graph = _repair_workflow_graph(
        enhance_workflow_graph_via_llm(roles, task_description),
        roles,
        task_description,
    )
    yaml_content = _build_workflow_yaml(roles, experts, team_name, task_description, workflow_graph)
    workflow_layout = _build_workflow_layout(workflow_graph, experts)

    workflow_meta = workflow_graph.get("meta") or {}

    return {
        "oasis_experts": experts,
        "internal_agents": agents,
        "workflow_graph": workflow_graph,
        "workflow_layout": workflow_layout,
        "yaml_workflow": yaml_content,
        "summary": {
            "total_roles": len(roles),
            "preset_matched": sum(1 for e in experts if e.get("matched_preset")),
            "custom_generated": sum(1 for e in experts if e.get("source") == "generated"),
            "team_name": team_name,
            "dag_enhanced": bool(
                workflow_meta.get("llm_enhanced")
                or workflow_meta.get("selector_nodes")
                or workflow_meta.get("conditional_edges")
                or workflow_meta.get("manual_nodes", 0) >= 2
            ),
            "workflow_mode": workflow_meta.get("mode", "heuristic"),
            "workflow_nodes": workflow_meta.get("expert_nodes", 0) + workflow_meta.get("manual_nodes", 0),
            "selector_nodes": workflow_meta.get("selector_nodes", 0),
            "conditional_edges": workflow_meta.get("conditional_edges", 0),
            "review_loops": workflow_meta.get("review_loops", 0),
        },
    }


def _yaml_quote(value: Any) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _build_workflow_layout(
    workflow_graph: dict[str, Any],
    experts: list[dict],
) -> dict[str, Any]:
    """Build a lightweight visual layout directly from ClawCross Creator graph data."""
    plan = workflow_graph.get("plan") or []
    edges = workflow_graph.get("edges") or []
    conditional_edges = workflow_graph.get("conditional_edges") or []
    selector_edges = workflow_graph.get("selector_edges") or []
    selector_ids = {
        str(step.get("id"))
        for step in plan
        if step.get("selector")
    } | {
        str(entry.get("source"))
        for entry in selector_edges
        if isinstance(entry, dict) and entry.get("source")
    }

    role_to_expert = {expert.get("name"): expert for expert in experts}

    nodes: list[dict[str, Any]] = []
    step_ids_in_order: list[str] = []
    for step in plan:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "").strip()
        if not step_id:
            continue
        step_ids_in_order.append(step_id)

        if step.get("type") == "manual":
            author = str(step.get("author") or "主持人")
            if author == "begin":
                name = "开始"
                emoji = "🚀"
            elif author == "bend":
                name = "结束"
                emoji = "🏁"
            else:
                name = "手动注入"
                emoji = "📝"
            nodes.append({
                "id": step_id,
                "type": "manual",
                "tag": "manual",
                "name": name,
                "emoji": emoji,
                "author": author,
                "content": step.get("content", ""),
                "x": 0,
                "y": 0,
            })
            continue

        role_name = step.get("performing_role") or "Expert"
        expert = role_to_expert.get(role_name, {})
        nodes.append({
            "id": step_id,
            "type": "expert",
            "tag": expert.get("tag", ""),
            "name": role_name,
            "emoji": expert.get("emoji", "⭐"),
            "temperature": expert.get("temperature", 0.7),
            "source": expert.get("source", ""),
            "content": step.get("instruction", ""),
            "isSelector": step_id in selector_ids,
            "x": 0,
            "y": 0,
        })

    step_id_to_node = {node["id"]: node for node in nodes}
    fixed_edges = []
    for edge in edges:
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            source = str(edge[0])
            target = str(edge[1])
        else:
            continue
        if source in step_id_to_node and target in step_id_to_node:
            fixed_edges.append({"source": source, "target": target})

    preds: dict[str, list[str]] = {step_id: [] for step_id in step_ids_in_order}
    for edge in fixed_edges:
        preds.setdefault(edge["target"], []).append(edge["source"])

    layer: dict[str, int] = {}
    visiting: set[str] = set()

    def get_layer(step_id: str) -> int:
        if step_id in layer:
            return layer[step_id]
        if step_id in visiting:
            layer[step_id] = 0
            return 0
        visiting.add(step_id)
        dependencies = preds.get(step_id, [])
        if not dependencies:
            layer[step_id] = 0
        else:
            layer[step_id] = max(get_layer(dep) for dep in dependencies) + 1
        visiting.discard(step_id)
        return layer[step_id]

    for step_id in step_ids_in_order:
        get_layer(step_id)

    soft_preds: dict[str, list[str]] = {step_id: [] for step_id in step_ids_in_order}
    for entry in selector_edges:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "")
        for target in (entry.get("choices") or {}).values():
            target_id = str(target)
            if source in step_id_to_node and target_id in soft_preds:
                soft_preds[target_id].append(source)
    for entry in conditional_edges:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "")
        then_target = str(entry.get("then") or "")
        else_target = str(entry.get("else") or "") if entry.get("else") else ""
        if source in step_id_to_node and then_target in soft_preds:
            soft_preds[then_target].append(source)
        if source in step_id_to_node and else_target in soft_preds:
            soft_preds[else_target].append(source)

    for step_id in step_ids_in_order:
        if layer.get(step_id, 0) != 0:
            continue
        if not soft_preds.get(step_id):
            continue
        layer[step_id] = max(layer.get(source, 0) for source in soft_preds[step_id]) + 1

    layers: dict[int, list[str]] = {}
    for step_id in step_ids_in_order:
        layers.setdefault(layer.get(step_id, 0), []).append(step_id)

    margin_x = 56
    margin_y = 42
    gap_x = 248
    gap_y = 104

    node_y: dict[str, float] = {}
    for layer_index in sorted(layers.keys()):
        step_ids = layers[layer_index]
        if layer_index > 0:
            step_ids.sort(key=lambda item: sum(node_y.get(dep, 0.0) for dep in preds.get(item, [])) / max(len(preds.get(item, [])), 1))
        count = len(step_ids)
        total_h = max(0, (count - 1) * gap_y)
        y_start = margin_y + max(0, (420 - total_h) // 2)
        for row_index, step_id in enumerate(step_ids):
            node_y[step_id] = y_start + row_index * gap_y

    for layer_index, step_ids in layers.items():
        x = margin_x + layer_index * gap_x
        for step_id in step_ids:
            node = step_id_to_node.get(step_id)
            if node:
                node["x"] = x
                node["y"] = int(node_y.get(step_id, margin_y))

    cond_edges_out = []
    for entry in conditional_edges:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "")
        then_target = str(entry.get("then") or "")
        else_target = str(entry.get("else") or "") if entry.get("else") else ""
        if source in step_id_to_node and then_target in step_id_to_node:
            cond_edges_out.append({
                "source": source,
                "condition": entry.get("condition", ""),
                "then": then_target,
                "else": else_target if else_target in step_id_to_node else "",
            })

    selector_edges_out = []
    for entry in selector_edges:
        if not isinstance(entry, dict):
            continue
        source = str(entry.get("source") or "")
        if source not in step_id_to_node:
            continue
        mapped_choices = {}
        for key, target in (entry.get("choices") or {}).items():
            target_id = str(target)
            if target_id in step_id_to_node:
                mapped_choices[str(key)] = target_id
        if mapped_choices:
            selector_edges_out.append({"source": source, "choices": mapped_choices})

    return {
        "nodes": nodes,
        "edges": fixed_edges,
        "conditionalEdges": cond_edges_out,
        "selectorEdges": selector_edges_out,
        "groups": [],
        "settings": {
            "repeat": False,
            "max_rounds": 5,
            "cluster_threshold": 150,
        },
    }


def _build_workflow_yaml(
    roles: list[ExtractedRole],
    experts: list[dict],
    team_name: str,
    task_description: str,
    workflow_graph: dict[str, Any] | None = None,
) -> str:
    """Build an OASIS v2 YAML workflow from a ClawCross Studio-style graph."""
    role_to_tag = {role.role_name: expert["tag"] for role, expert in zip(roles, experts)}
    role_to_index = {role.role_name: idx + 1 for idx, role in enumerate(roles)}

    if not workflow_graph:
        workflow_graph = _build_default_workflow_graph(roles, task_description)

    lines = [
        f"# ClawCross Creator auto-generated workflow for: {team_name}",
        f"# Task: {task_description[:100]}",
        f"# Generated at: {datetime.now(timezone.utc).isoformat()}",
        f"# Workflow mode: {(workflow_graph.get('meta') or {}).get('mode', 'heuristic')}",
        "",
        "version: 2",
        "repeat: false",
        f"name: {_slugify(team_name) or 'team'}_workflow",
        f"topic: {_yaml_quote(task_description[:200])}",
        "mode: execute",
        "rounds: 1",
        "plan:",
    ]

    for step in workflow_graph.get("plan") or []:
        step_id = step.get("id")
        if not step_id:
            continue

        if step.get("type") == "manual":
            lines.append(f"  - id: {step_id}")
            lines.append("    manual:")
            lines.append(f"      author: {step.get('author', '主持人')}")
            lines.append(f"      content: {_yaml_quote(step.get('content', ''))}")
            continue

        role_name = step.get("performing_role")
        tag = role_to_tag.get(role_name)
        if not role_name or not tag:
            continue
        instance_index = role_to_index.get(role_name, 1)
        expert_ref = f"{tag}#temp#{instance_index}"
        lines.append(f"  - id: {step_id}")
        lines.append(f"    expert: {_yaml_quote(expert_ref)}")
        if step.get("instruction"):
            lines.append(f"    instruction: {_yaml_quote(step['instruction'])}")
        if step.get("selector"):
            lines.append("    selector: true")

    edges = workflow_graph.get("edges") or []
    lines.append("edges:")
    for source, target in edges:
        lines.append(f"  - [{source}, {target}]")

    conditional_edges = workflow_graph.get("conditional_edges") or []
    if conditional_edges:
        lines.append("conditional_edges:")
        for entry in conditional_edges:
            lines.append(f"  - source: {entry['source']}")
            lines.append(f"    condition: {_yaml_quote(entry['condition'])}")
            lines.append(f"    then: {entry['then']}")
            if entry.get("else"):
                lines.append(f"    else: {entry['else']}")

    selector_edges = workflow_graph.get("selector_edges") or []
    if selector_edges:
        lines.append("selector_edges:")
        for entry in selector_edges:
            lines.append(f"  - source: {entry['source']}")
            lines.append("    choices:")
            for key in sorted(entry.get("choices", {}).keys(), key=lambda item: (not str(item).isdigit(), int(item) if str(item).isdigit() else str(item))):
                lines.append(f"      {key}: {entry['choices'][key]}")

    return "\n".join(lines) + "\n"


# ──────────────────────────────────────────────────────────────
# ZIP Assembly — produce Clawcross-compatible snapshot
# ──────────────────────────────────────────────────────────────

_ARCHIVE_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ARCHIVE_PATH_RE = re.compile(r"[\\/]+")
_ARCHIVE_ASCII_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_archive_segment(value: str, default: str = "team") -> str:
    """Normalize a user-provided name for zip paths and filenames."""
    text = str(value or "").strip()
    text = _ARCHIVE_CONTROL_RE.sub("_", text)
    text = _ARCHIVE_PATH_RE.sub("_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    text = text.strip(" ._")
    return text or default


def build_team_creator_download_name(team_name: str, timestamp: str) -> str:
    """Build the user-visible ClawCross Creator zip filename."""
    safe_name = sanitize_archive_segment(team_name, default="team")
    return f"team_{safe_name}_creator_{timestamp}.zip"


def build_attachment_content_disposition(filename: str) -> str:
    """Return an ASCII-safe attachment header with UTF-8 filename support."""
    clean_name = str(filename or "").replace("\r", " ").replace("\n", " ").strip()
    if not clean_name:
        clean_name = "download.zip"

    base_name, dot, ext = clean_name.rpartition(".")
    name_part = base_name if dot else clean_name
    ext_part = ext if dot else ""

    ascii_base = re.sub(r"_+", "_", _ARCHIVE_ASCII_RE.sub("_", name_part)).strip("._") or "download"
    ascii_ext = re.sub(r"[^A-Za-z0-9]+", "", ext_part)
    ascii_name = f"{ascii_base}.{ascii_ext}" if ascii_ext else ascii_base

    encoded_name = quote(clean_name, safe="")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'


def build_team_zip(
    team_config: dict[str, Any],
    team_name: str,
) -> bytes:
    """Assemble a ZIP file in the same format as /teams/snapshot/download.

    The ZIP can be imported via /teams/snapshot/upload or ClawCross Hub.
    """
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. internal_agents.json — agent metadata (no session field)
        agents = team_config.get("internal_agents", [])
        zf.writestr(
            "internal_agents.json",
            json.dumps(agents, ensure_ascii=False, indent=2),
        )

        # 2. oasis_experts.json — persona definitions
        experts = team_config.get("oasis_experts", [])
        clean_experts = []
        for e in experts:
            clean = {
                "name": e["name"],
                "tag": e["tag"],
                "persona": e["persona"],
                "temperature": e.get("temperature", 0.7),
            }
            if e.get("name_en"):
                clean["name_en"] = e["name_en"]
            clean_experts.append(clean)
        zf.writestr(
            "oasis_experts.json",
            json.dumps(clean_experts, ensure_ascii=False, indent=2),
        )

        # 3. YAML workflow
        yaml_content = team_config.get("yaml_workflow", "")
        if yaml_content:
            safe_name = sanitize_archive_segment(team_name, default="workflow")
            zf.writestr(
                f"oasis/yaml/{safe_name}.yaml",
                yaml_content,
            )

    buf.seek(0)
    return buf.read()


# ──────────────────────────────────────────────────────────────
# High-level pipeline orchestration
# ──────────────────────────────────────────────────────────────

@dataclass
class BuildJob:
    """Tracks the state of a team build job."""
    job_id: str
    task_description: str
    team_name: str
    owner_id: str = ""
    status: str = "pending"  # pending | running | complete | failed
    discovered_pages: list[dict] = field(default_factory=list)
    extracted_roles: list[dict] = field(default_factory=list)
    team_config: dict = field(default_factory=dict)
    error: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self, include_payload: bool = False) -> dict:
        data = {
            "job_id": self.job_id,
            "task_description": self.task_description,
            "team_name": self.team_name,
            "status": self.status,
            "discovered_pages": self.discovered_pages,
            "extracted_roles_count": len(self.extracted_roles),
            "team_config_summary": self.team_config.get("summary") if self.team_config else None,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_payload:
            data["extracted_roles"] = self.extracted_roles
            data["team_config"] = self.team_config
        return data


def _row_to_job(row: sqlite3.Row) -> BuildJob:
    return BuildJob(
        job_id=row["job_id"],
        task_description=row["task_description"],
        team_name=row["team_name"],
        owner_id=row["owner_id"],
        status=row["status"],
        discovered_pages=_json_loads(row["discovered_pages_json"], []),
        extracted_roles=_json_loads(row["extracted_roles_json"], []),
        team_config=_json_loads(row["team_config_json"], {}),
        error=row["error"] or "",
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
    )


def _save_job(job: BuildJob, *, db_path: str | Path | None = None) -> BuildJob:
    conn = _connect_jobs_db(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO team_creator_jobs (
                    job_id, owner_id, task_description, team_name, status,
                    discovered_pages_json, extracted_roles_json, team_config_json,
                    error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.owner_id,
                    job.task_description,
                    job.team_name,
                    job.status,
                    _json_dumps(job.discovered_pages),
                    _json_dumps(job.extracted_roles),
                    _json_dumps(job.team_config),
                    job.error,
                    job.created_at,
                    job.updated_at,
                ),
            )
        return job
    finally:
        conn.close()


def create_job(task_description: str, team_name: str, owner_id: str = "", db_path: str | Path | None = None) -> BuildJob:
    """Create a new build job and return it."""
    job_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    job = BuildJob(
        job_id=job_id,
        task_description=task_description,
        team_name=team_name,
        owner_id=owner_id,
        created_at=now,
        updated_at=now,
    )
    return _save_job(job, db_path=db_path)


def update_job(
    job_id: str,
    *,
    owner_id: str | None = None,
    status: str | None = None,
    discovered_pages: list[dict] | None = None,
    extracted_roles: list[dict] | None = None,
    team_config: dict | None = None,
    error: str | None = None,
    team_name: str | None = None,
    task_description: str | None = None,
    db_path: str | Path | None = None,
) -> BuildJob | None:
    job = get_job(job_id, owner_id=owner_id, db_path=db_path)
    if not job:
        return None
    if status is not None:
        job.status = status
    if discovered_pages is not None:
        job.discovered_pages = discovered_pages
    if extracted_roles is not None:
        job.extracted_roles = extracted_roles
    if team_config is not None:
        job.team_config = team_config
    if error is not None:
        job.error = error
    if team_name is not None:
        job.team_name = team_name
    if task_description is not None:
        job.task_description = task_description
    job.updated_at = datetime.now(timezone.utc).isoformat()
    return _save_job(job, db_path=db_path)


def get_job(job_id: str, owner_id: str | None = None, db_path: str | Path | None = None) -> BuildJob | None:
    conn = _connect_jobs_db(db_path)
    try:
        if owner_id is None:
            row = conn.execute(
                "SELECT * FROM team_creator_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM team_creator_jobs WHERE job_id = ? AND owner_id = ?",
                (job_id, owner_id),
            ).fetchone()
        return _row_to_job(row) if row else None
    finally:
        conn.close()


def list_jobs(owner_id: str | None = None, limit: int | None = None, db_path: str | Path | None = None) -> list[dict]:
    """Return all jobs sorted by creation time (newest first)."""
    conn = _connect_jobs_db(db_path)
    try:
        query = "SELECT * FROM team_creator_jobs"
        params: list[Any] = []
        if owner_id is not None:
            query += " WHERE owner_id = ?"
            params.append(owner_id)
        query += " ORDER BY created_at DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [_row_to_job(row).to_dict() for row in rows]
    finally:
        conn.close()


def build_from_roles(
    roles_json: list[dict],
    team_name: str,
    task_description: str = "",
) -> dict[str, Any]:
    """Directly build a team from manually provided role data (skip discovery+extraction).

    This is the most useful entry point for the MVP — users can paste
    crawled/structured role data and immediately get a team config + ZIP.
    """
    roles = []
    for item in roles_json:
        if not isinstance(item, dict):
            continue
        name = str(item.get("role_name", "")).strip()
        if not name:
            continue
        roles.append(ExtractedRole(
            role_name=name,
            personality_traits=item.get("personality_traits") or [],
            primary_responsibilities=item.get("primary_responsibilities") or [],
            depends_on=item.get("depends_on") or [],
            tools_used=item.get("tools_used") or [],
            expert_tag=str(item.get("_expert_tag", "")).strip(),
        ))

    if not roles:
        raise ValueError("No valid roles found in input data")

    team_config = map_roles_to_team(roles, team_name, task_description)
    return team_config


# ──────────────────────────────────────────────────────────────
# External Skill Import — colleague-skill & supervisor (mentor)
# ──────────────────────────────────────────────────────────────

def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _clean_markdown_item(line: str) -> str:
    cleaned = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", str(line or "").strip())
    return cleaned.strip()


def _parse_markdown_sections(markdown: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_section = ""
    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        if line.startswith("## "):
            current_section = line[3:].strip()
            sections.setdefault(current_section, [])
            continue
        if current_section:
            sections.setdefault(current_section, []).append(line)
    return sections


def _extract_colleague_responsibilities(work_md: str) -> list[str]:
    sections = _parse_markdown_sections(work_md)
    responsibilities: list[str] = []

    for line in sections.get("职责范围", []):
        if line.startswith("### "):
            continue
        item = _clean_markdown_item(line)
        if not item or item.endswith("："):
            continue
        if line.lstrip().startswith(("-", "*")) or "负责" in item or "维护" in item or "边界" in item:
            responsibilities.append(item)

    for line in sections.get("工作流程", []):
        if line.startswith("### "):
            continue
        item = _clean_markdown_item(line)
        if not item or item.endswith("："):
            continue
        if re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", line):
            responsibilities.append(item)

    if responsibilities:
        return _dedupe_preserve_order(responsibilities)[:8]

    fallback: list[str] = []
    for raw_line in str(work_md or "").splitlines():
        line = raw_line.strip()
        if not line or line == "---" or line.startswith("#"):
            continue
        item = _clean_markdown_item(line)
        if not item or item.endswith("："):
            continue
        if re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", raw_line):
            fallback.append(item)
    return _dedupe_preserve_order(fallback)[:8]


def _mentor_source_url(mentor_json: dict[str, Any]) -> str:
    profile = mentor_json.get("profile") or {}
    website = str(profile.get("website") or "").strip()
    if website:
        return website
    source_materials = mentor_json.get("source_materials") or {}
    for key in ("websites_visited", "source_urls", "websites"):
        for item in _coerce_str_list(source_materials.get(key)):
            if item:
                return item
    return "supervisor-mentor"


_COLLEAGUE_DISTILL_PROMPT = (
    "You are converting Feishu/Lark chat materials into colleague-skill artifacts for Clawcross ClawCross Creator.\n\n"
    "Return ONLY valid JSON with this schema:\n"
    '{{'
    '"persona_md": "# ...",'
    '"work_md": "# ...",'
    '"personality_tags": ["..."],'
    '"culture_tags": ["..."],'
    '"impression": "...",'
    '"evidence_summary": "..."'
    '}}\n\n'
    "Rules:\n"
    "- Infer only from the supplied evidence. Do not fabricate unknown background details.\n"
    "- Keep the output in Chinese unless the source material is clearly dominated by another language.\n"
    "- persona_md must be markdown and include these sections in order:\n"
    "  1. # {name} — Persona\n"
    "  2. ## Layer 0：核心性格\n"
    "  3. ## Layer 1：沟通风格\n"
    "  4. ## Layer 2：决策偏好\n"
    "  5. ## Layer 3：协作模式\n"
    "  6. ## Layer 4：压力与边界\n"
    "- work_md must be markdown and include these sections in order:\n"
    "  1. # {name} — Work Skill\n"
    "  2. ## 职责范围\n"
    "  3. ## 关键上下文\n"
    "  4. ## 工作流程\n"
    "  5. ## 交付偏好\n"
    "- Each section should be concise, practical, and import-ready.\n"
    "- personality_tags and culture_tags should each contain 1-6 short items.\n"
    "- impression should be one sentence that a teammate can read quickly.\n"
    "- evidence_summary should mention what evidence the distillation relied on.\n\n"
    "meta_json:\n{meta_json}\n\n"
    "messages_text:\n{messages_text}"
)


def _truncate_colleague_messages(messages_text: str, limit: int = 16000) -> str:
    cleaned = str(messages_text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    head = cleaned[: int(limit * 0.7)].rstrip()
    tail = cleaned[-int(limit * 0.2):].lstrip()
    return f"{head}\n\n[... truncated ...]\n\n{tail}"


def _message_evidence_lines(messages_text: str, *, limit: int = 6) -> list[str]:
    evidence: list[str] = []
    for raw_line in str(messages_text or "").splitlines():
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^\[[^\]]+\]\s*", "", line)
        line = re.sub(r"^\d{1,2}:\d{2}\s*", "", line)
        line = re.sub(r"^\d+[.)]\s*", "", line)
        if len(line) < 6:
            continue
        evidence.append(line[:180])
    return _dedupe_preserve_order(evidence)[:limit]


def _fallback_colleague_distillation(meta_json: dict[str, Any], messages_text: str) -> dict[str, Any]:
    profile = meta_json.get("profile") or {}
    tags = meta_json.get("tags") or {}
    name = str(meta_json.get("name") or "同事").strip() or "同事"
    role = str(profile.get("role") or "").strip()
    company = str(profile.get("company") or "").strip()
    level = str(profile.get("level") or "").strip()
    impression = str(meta_json.get("impression") or "").strip()
    personality_tags = _dedupe_preserve_order(
        _coerce_str_list(tags.get("personality")) or ["直接", "重执行"]
    )[:6]
    culture_tags = _dedupe_preserve_order(
        _coerce_str_list(tags.get("culture")) or ["先解决问题", "对结果负责"]
    )[:6]
    evidence_lines = _message_evidence_lines(messages_text)

    persona_md = "\n\n".join([
        f"# {name} — Persona",
        "## Layer 0：核心性格\n" + "\n".join(
            [f"- {personality_tags[0]}，先给结论再展开。"] +
            ([f"- {impression}"] if impression else ["- 说话和做事都围绕交付结果。"])
        ),
        "## Layer 1：沟通风格\n" + "\n".join([
            "- 信息密度高，不喜欢空话。",
            "- 遇到模糊需求会先追问边界、优先级和可落地方案。",
        ]),
        "## Layer 2：决策偏好\n" + "\n".join([
            "- 更看重直接证据、线上影响和交付风险。",
            "- 倾向先把最关键的问题拆出来，再决定要不要扩展范围。",
        ]),
        "## Layer 3：协作模式\n" + "\n".join([
            f"- 默认以{role or '本职工作'}为中心推进，和上下游快速对齐。",
            "- 需要其他人配合时，会明确输入、截止时间和验收标准。",
        ]),
        "## Layer 4：压力与边界\n" + "\n".join([
            "- 反感重复解释已经明确过的背景和约束。",
            "- 面对紧急问题时优先止血，再补根因和后续优化。",
        ]),
    ])

    context_lines = []
    if company:
        context_lines.append(f"- 公司/团队：{company}")
    if role:
        context_lines.append(f"- 岗位：{role}")
    if level:
        context_lines.append(f"- 层级：{level}")
    context_lines.extend(f"- 对话证据：{line}" for line in evidence_lines[:3])
    if not context_lines:
        context_lines.append("- 关键上下文主要来自飞书消息语料。")

    workflow_lines = [f"{idx}. {line}" for idx, line in enumerate(evidence_lines[:4], 1)]
    if not workflow_lines:
        workflow_lines = [
            "1. 先确认背景、边界条件和优先级。",
            "2. 给出能落地的短期方案，再补长期优化。",
        ]

    work_md = "\n\n".join([
        f"# {name} — Work Skill",
        "## 职责范围\n" + "\n".join([
            f"- 负责与{role or '当前岗位'}相关的核心交付与协作推进。",
            "- 在信息不完整时补齐上下文，推动问题收敛到可执行方案。",
        ]),
        "## 关键上下文\n" + "\n".join(context_lines),
        "## 工作流程\n" + "\n".join(workflow_lines),
        "## 交付偏好\n" + "\n".join([
            "- 输出偏向结论先行，必要时补充证据和风险。",
            "- 交付物要可执行、可验收，而不是只给方向性建议。",
        ]),
    ])

    return {
        "persona_md": persona_md,
        "work_md": work_md,
        "personality_tags": personality_tags,
        "culture_tags": culture_tags,
        "impression": impression or f"{name} 说话直接，执行导向明显。",
        "evidence_summary": f"Fallback distillation based on {len(evidence_lines)} extracted chat evidence lines.",
    }


def distill_colleague_skill_artifacts(
    meta_json: dict[str, Any],
    messages_text: str,
) -> dict[str, Any]:
    """Distill colleague persona/work artifacts from collected chat materials."""
    if not isinstance(meta_json, dict) or not meta_json:
        raise ValueError("meta_json is required")
    name = str(meta_json.get("name") or "").strip()
    if not name:
        raise ValueError("meta_json.name is required")

    prompt = _COLLEAGUE_DISTILL_PROMPT.format(
        name=name,
        meta_json=_json_dumps(meta_json),
        messages_text=_truncate_colleague_messages(messages_text),
    )

    try:
        from services.llm_factory import create_chat_model

        llm = create_chat_model(temperature=0.2, max_tokens=4096, timeout=120)
        response = llm.invoke(prompt)
        raw_text = _llm_content_to_text(response.content if hasattr(response, "content") else str(response))
        payload = _extract_json_payload(raw_text)
        if not isinstance(payload, dict):
            raise ValueError("LLM did not return a valid JSON object")

        persona_md = str(payload.get("persona_md") or "").strip()
        work_md = str(payload.get("work_md") or "").strip()
        if not persona_md or not work_md:
            raise ValueError("LLM distillation payload missing persona_md/work_md")

        return {
            "persona_md": persona_md,
            "work_md": work_md,
            "personality_tags": _dedupe_preserve_order(_coerce_str_list(payload.get("personality_tags")))[:6],
            "culture_tags": _dedupe_preserve_order(_coerce_str_list(payload.get("culture_tags")))[:6],
            "impression": str(payload.get("impression") or "").strip(),
            "evidence_summary": str(payload.get("evidence_summary") or "").strip(),
        }
    except Exception as exc:
        _log.warning("Colleague distillation failed, using fallback: %s", exc)
        return _fallback_colleague_distillation(meta_json, messages_text)

def import_colleague_skill(
    meta_json: dict,
    persona_md: str,
    work_md: str = "",
    team_name: str = "",
    task_description: str = "",
) -> dict[str, Any]:
    """Import a colleague-skill output into Clawcross's ClawCross Creator.

    Accepts the output files from https://github.com/titanwings/colleague-skill:
      - meta.json   (parsed dict)
      - persona.md  (raw markdown string — the 5-layer persona)
      - work.md     (raw markdown string — optional work skill)

    Returns a team_config dict identical to what build_from_roles() returns,
    but with the *full* persona.md content used directly (not compressed by
    _build_persona()).
    """
    if not meta_json or not isinstance(meta_json, dict):
        raise ValueError("meta_json is required and must be a dict")

    name = str(meta_json.get("name", "")).strip()
    if not name:
        raise ValueError("meta_json.name is required")

    slug = str(meta_json.get("slug", "")).strip() or _slugify(name)
    profile = meta_json.get("profile") or {}
    tags = meta_json.get("tags") or {}

    # Build personality traits from tags
    personality_traits: list[str] = []
    for tag_list in [tags.get("personality", []), tags.get("culture", [])]:
        if isinstance(tag_list, list):
            personality_traits.extend(str(t) for t in tag_list if t)

    # Build responsibilities from work.md, favoring real duty/process sections
    responsibilities = _extract_colleague_responsibilities(work_md)

    # Build identity string
    identity_parts = []
    if profile.get("company"):
        identity_parts.append(str(profile["company"]))
    if profile.get("level"):
        identity_parts.append(str(profile["level"]))
    if profile.get("role"):
        identity_parts.append(str(profile["role"]))
    identity = " ".join(identity_parts) if identity_parts else "同事"

    # Create the ExtractedRole — this is the standard Clawcross data model
    role = ExtractedRole(
        role_name=name,
        personality_traits=_dedupe_preserve_order(personality_traits)[:8],
        primary_responsibilities=responsibilities[:8],
        depends_on=[],
        tools_used=[],
        source_url=f"colleague-skill:{slug}",
    )

    # Build team config via map_roles_to_team, then **override** the persona
    # with the full colleague-skill persona.md content (not the compressed version)
    effective_team_name = team_name or f"{name} 团队"
    effective_task = task_description or f"{name} 的工作能力与人物性格"

    team_config = map_roles_to_team([role], effective_team_name, effective_task)

    # Override persona: use full persona.md + work.md (the key design decision)
    if team_config.get("oasis_experts") and persona_md:
        full_persona_parts = []
        if work_md:
            full_persona_parts.append(f"## PART A：工作能力\n\n{work_md.strip()}")
            full_persona_parts.append("---")
        full_persona_parts.append(f"## PART B：人物性格\n\n{persona_md.strip()}")
        full_persona_parts.append("---")
        full_persona_parts.append(
            "## 运行规则\n\n"
            "1. 先由 PART B 判断：用什么态度接这个任务？\n"
            "2. 再由 PART A 执行：用你的技术能力完成任务\n"
            "3. 输出时始终保持 PART B 的表达风格\n"
            "4. PART B Layer 0 的规则优先级最高，任何情况下不得违背"
        )

        team_config["oasis_experts"][0]["persona"] = "\n\n".join(full_persona_parts)
        team_config["oasis_experts"][0]["source"] = "colleague-skill"
        team_config["oasis_experts"][0]["name"] = name

    # Enrich summary
    if team_config.get("summary"):
        team_config["summary"]["import_source"] = "colleague-skill"
        team_config["summary"]["colleague_meta"] = {
            "name": name,
            "slug": slug,
            "identity": identity,
            "version": meta_json.get("version", "v1"),
            "company": str(profile.get("company") or ""),
            "level": str(profile.get("level") or ""),
            "role": str(profile.get("role") or ""),
            "gender": str(profile.get("gender") or ""),
            "mbti": profile.get("mbti", ""),
            "impression": str(meta_json.get("impression", "")),
            "knowledge_sources": meta_json.get("knowledge_sources") or [],
            "corrections_count": int(meta_json.get("corrections_count") or 0),
        }

    return team_config


def import_mentor_skill(
    mentor_json: dict,
    skill_md: str = "",
    team_name: str = "",
    task_description: str = "",
) -> dict[str, Any]:
    """Import a supervisor (mentor) skill output into Clawcross's ClawCross Creator.

    Accepts the output files from https://github.com/ybq22/supervisor:
      - {name}.json  (parsed dict — the mentor profile JSON)
      - SKILL.md     (raw markdown string — the generated mentor skill)

    Returns a team_config dict identical to what build_from_roles() returns,
    but with the *full* SKILL.md / JSON-derived persona used directly.
    """
    if not mentor_json or not isinstance(mentor_json, dict):
        raise ValueError("mentor_json is required and must be a dict")

    # Extract profile info from the nested JSON structure
    profile = mentor_json.get("profile") or {}
    meta = mentor_json.get("meta") or {}
    research = mentor_json.get("research") or {}
    style = mentor_json.get("style") or {}
    achievements = mentor_json.get("achievements") or {}
    source_materials = mentor_json.get("source_materials") or {}

    name = (
        str(profile.get("name_zh", "")).strip()
        or str(profile.get("name_en", "")).strip()
        or str(meta.get("mentor_name", "")).strip()
    )
    if not name:
        raise ValueError("Cannot determine mentor name from JSON")

    name_en = str(profile.get("name_en", "")).strip()
    institution = str(profile.get("institution") or meta.get("affiliation", "")).strip()
    position = str(profile.get("position", "")).strip()

    # Build personality traits from research style + academic values
    personality_traits: list[str] = []
    rs = style.get("research_style") or {}
    if rs.get("type"):
        personality_traits.append(str(rs["type"]))
    if rs.get("keywords") and isinstance(rs["keywords"], list):
        personality_traits.extend(str(k) for k in rs["keywords"][:5])
    if style.get("academic_values") and isinstance(style["academic_values"], list):
        personality_traits.extend(str(v) for v in style["academic_values"][:5])
    communication_style = style.get("communication_style") or {}
    if communication_style.get("tone"):
        personality_traits.append(str(communication_style["tone"]))

    # Build responsibilities from research fields
    responsibilities: list[str] = []
    for field in research.get("primary_fields", []):
        responsibilities.append(f"研究方向：{field}")
    if research.get("research_summary"):
        responsibilities.append(str(research["research_summary"]))
    for area in style.get("expertise_areas") or []:
        responsibilities.append(f"专长领域：{area}")
    for pub in (research.get("key_publications") or [])[:3]:
        if isinstance(pub, dict) and pub.get("summary"):
            responsibilities.append(str(pub["summary"])[:100])

    # Create the ExtractedRole
    role = ExtractedRole(
        role_name=name,
        personality_traits=_dedupe_preserve_order(personality_traits)[:8],
        primary_responsibilities=_dedupe_preserve_order(responsibilities)[:8],
        depends_on=[],
        tools_used=[],
        source_url=_mentor_source_url(mentor_json),
    )

    effective_team_name = team_name or f"{name} 导师团队"
    effective_task = task_description or f"{name} 的学术指导与研究风格"

    team_config = map_roles_to_team([role], effective_team_name, effective_task)

    # Override persona: use full SKILL.md or build rich persona from JSON
    if team_config.get("oasis_experts"):
        if skill_md:
            # Use the full SKILL.md content as persona (it contains the system prompt)
            # Strip the YAML frontmatter if present
            persona_content = skill_md.strip()
            fm_match = re.match(r"^---\s*\n[\s\S]*?\n---\s*\n", persona_content)
            if fm_match:
                persona_content = persona_content[fm_match.end():].strip()
            team_config["oasis_experts"][0]["persona"] = persona_content
        else:
            # Build persona from JSON fields
            persona_parts = [f"# {name} AI Mentor\n"]
            persona_parts.append(f"你是 {name}")
            if institution:
                persona_parts[-1] += f"（{institution}"
                if position:
                    persona_parts[-1] += f" {position}"
                persona_parts[-1] += "）"
            persona_parts[-1] += "的 AI 助手，专注于提供与其研究风格和学术观点一致的指导。\n"

            if research.get("research_summary"):
                persona_parts.append(f"## 研究背景\n\n{research['research_summary']}\n")

            if research.get("primary_fields"):
                persona_parts.append("## 研究领域\n\n" + "、".join(research["primary_fields"]) + "\n")

            if rs.get("description"):
                persona_parts.append(f"## 研究风格\n\n{rs['description']}\n")

            cs = style.get("communication_style") or {}
            if cs.get("tone") or cs.get("characteristics"):
                persona_parts.append("## 沟通风格\n")
                if cs.get("tone"):
                    persona_parts.append(f"- 语气：{cs['tone']}")
                if cs.get("characteristics"):
                    persona_parts.append(f"- 特点：{cs['characteristics']}")
                persona_parts.append("")

            if style.get("academic_values"):
                persona_parts.append("## 学术价值观\n\n" + "\n".join(
                    f"- {v}" for v in style["academic_values"]
                ) + "\n")

            academic_service = achievements.get("academic_service") or []
            if academic_service:
                persona_parts.append("## 学术职务\n\n" + "\n".join(
                    f"- {item}" for item in academic_service if str(item or "").strip()
                ) + "\n")

            honors = achievements.get("honors") or []
            if honors:
                persona_parts.append("## 主要荣誉\n\n" + "\n".join(
                    f"- {item}" for item in honors if str(item or "").strip()
                ) + "\n")

            pubs = research.get("key_publications") or []
            if pubs:
                persona_parts.append("## 代表性贡献\n")
                for i, pub in enumerate(pubs[:5], 1):
                    if isinstance(pub, dict):
                        title = pub.get("title", "")
                        venue = pub.get("venue", "")
                        year = pub.get("year", "")
                        summary = pub.get("summary", "")
                        persona_parts.append(f"{i}. **{title}** ({venue} {year})")
                        if summary:
                            persona_parts.append(f"   - {summary}")
                persona_parts.append("")

            source_urls = []
            for key in ("websites_visited", "source_urls", "websites"):
                source_urls.extend(_coerce_str_list(source_materials.get(key)))
            if source_urls:
                persona_parts.append("## 参考来源\n\n" + "\n".join(
                    f"- {item}" for item in _dedupe_preserve_order(source_urls)[:8]
                ) + "\n")

            persona_parts.append(
                "## 约束条件\n\n"
                "- 不编造导师未发表的观点\n"
                "- 超出专业领域时明确说明\n"
                "- 保持学术严谨性\n"
                "- 优先引用导师的真实研究"
            )

            team_config["oasis_experts"][0]["persona"] = "\n".join(persona_parts)

        team_config["oasis_experts"][0]["source"] = "supervisor-mentor"
        team_config["oasis_experts"][0]["name"] = name
        if name_en:
            team_config["oasis_experts"][0]["name_en"] = name_en

    # Enrich summary
    if team_config.get("summary"):
        team_config["summary"]["import_source"] = "supervisor-mentor"
        team_config["summary"]["mentor_meta"] = {
            "name": name,
            "name_en": name_en,
            "institution": institution,
            "position": position,
            "primary_fields": research.get("primary_fields", []),
            "papers_count": len(research.get("key_publications", [])),
            "languages": profile.get("languages", []),
            "website": str(profile.get("website") or ""),
            "source_materials": source_materials,
        }

    return team_config


# ──────────────────────────────────────────────────────────────────────────────
# Personal skill import (前任 / crush / yourself / 群友 等关系型 skill)
# Compatible with:
#   - https://github.com/therealXiaomanChu/ex-skill  (前任.skill)
#   - https://github.com/titanwings/ex-skill
#   - https://github.com/notdog1998/yourself-skill
#   - https://github.com/xiaoheizi8/crush-skills
#   - https://github.com/Neko-Suwako/pig-skill        (群友.skill)
# All share: meta.json + persona.md + memory.md / self.md
# ──────────────────────────────────────────────────────────────────────────────

# Skill type → descriptive label used in persona section headers
_PERSONAL_SKILL_LABELS: dict[str, str] = {
    "ex": "关系记忆与回忆",
    "ex-skill": "关系记忆与回忆",
    "前任": "关系记忆与回忆",
    "crush": "暗恋记忆与互动",
    "crush-skill": "暗恋记忆与互动",
    "yourself": "自我记忆与经历",
    "yourself-skill": "自我记忆与经历",
    "pig": "群友互动与印象",
    "pig-skill": "群友互动与印象",
    "colleague": "工作能力与经历",
}


def import_personal_skill(
    meta_json: dict,
    persona_md: str,
    memory_md: str = "",
    skill_type: str = "ex",
    team_name: str = "",
    task_description: str = "",
) -> dict[str, Any]:
    """Import a personal/relationship-type skill into Clawcross's ClawCross Creator.

    Handles the family of GitHub skill repos that share the 5-layer persona format:
      - meta.json        (parsed dict)
      - persona.md       (raw markdown — 5-layer personality structure)
      - memory.md / self.md  (raw markdown — relationship/personal memories, optional)

    Supported skill_type values:
      "ex" / "ex-skill" / "前任"   — 前任.skill (therealXiaomanChu, titanwings)
      "crush" / "crush-skill"       — crush.skill (xiaoheizi8)
      "yourself" / "yourself-skill" — yourself.skill (notdog1998)
      "pig" / "pig-skill"           — 群友.skill (Neko-Suwako)

    Returns a team_config dict compatible with build_from_roles().
    """
    if not meta_json or not isinstance(meta_json, dict):
        raise ValueError("meta_json is required and must be a dict")

    name = str(meta_json.get("name", "")).strip()
    if not name:
        raise ValueError("meta_json.name is required")

    slug = str(meta_json.get("slug", "")).strip() or _slugify(name)
    profile = meta_json.get("profile") or {}

    # Normalise skill_type
    skill_type_norm = skill_type.lower().strip()
    memory_section_label = _PERSONAL_SKILL_LABELS.get(skill_type_norm, "记忆与经历")

    # Extract personality clues from profile
    personality_traits: list[str] = []
    if profile.get("mbti"):
        personality_traits.append(f"MBTI: {profile['mbti']}")
    if profile.get("zodiac"):
        personality_traits.append(f"星座: {profile['zodiac']}")
    # Infer from persona_md if possible (grab list items)
    for line in (persona_md or "").splitlines()[:60]:
        stripped = line.strip().lstrip("- ").strip()
        if stripped and len(stripped) < 40 and not stripped.startswith("#"):
            personality_traits.append(stripped)
        if len(personality_traits) >= 8:
            break

    # Build a basic identity description
    identity_parts: list[str] = []
    for field_key in ("occupation", "role", "city"):
        val = str(profile.get(field_key) or "").strip()
        if val:
            identity_parts.append(val)
    identity = "、".join(identity_parts) if identity_parts else "关系人物"

    role = ExtractedRole(
        role_name=name,
        personality_traits=_dedupe_preserve_order(personality_traits)[:8],
        primary_responsibilities=[identity] if identity else [],
        depends_on=[],
        tools_used=[],
        source_url=f"{skill_type_norm}-skill:{slug}",
    )

    effective_team_name = team_name or f"{name} 角色团队"
    effective_task = task_description or f"扮演 {name}，还原其人物性格与{memory_section_label}"

    team_config = map_roles_to_team([role], effective_team_name, effective_task)

    # Override persona with full 5-layer content
    if team_config.get("oasis_experts") and persona_md:
        parts: list[str] = []
        if memory_md:
            parts.append(f"## PART A：{memory_section_label}\n\n{memory_md.strip()}")
            parts.append("---")
        parts.append(f"## PART B：人物性格（五层人格）\n\n{persona_md.strip()}")
        parts.append("---")
        parts.append(
            "## 运行规则\n\n"
            "1. 先由 PART B Layer 0 检查：此请求是否合理？\n"
            "2. 由 PART B 整体判断：用什么情绪和态度回应？\n"
            "3. 如有 PART A，优先从中检索相关记忆后再回应\n"
            "4. 输出时始终保持 PART B 的语气、口癖和表达风格\n"
            "5. Layer 0 的规则优先级最高，任何情况下不得违背"
        )

        team_config["oasis_experts"][0]["persona"] = "\n\n".join(parts)
        team_config["oasis_experts"][0]["source"] = f"{skill_type_norm}-skill"
        team_config["oasis_experts"][0]["name"] = name

    if team_config.get("summary"):
        team_config["summary"]["import_source"] = f"{skill_type_norm}-skill"
        team_config["summary"]["personal_meta"] = {
            "name": name,
            "slug": slug,
            "skill_type": skill_type_norm,
            "identity": identity,
            "occupation": str(profile.get("occupation") or ""),
            "city": str(profile.get("city") or ""),
            "mbti": str(profile.get("mbti") or ""),
            "zodiac": str(profile.get("zodiac") or ""),
            "version": str(meta_json.get("version") or "v1"),
        }

    return team_config
