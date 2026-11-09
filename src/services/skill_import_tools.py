#!/usr/bin/env python3
"""Skill Import Tools — Python-native implementations for data collection.

Replaces the Node.js-based tools from colleague-skill and supervisor repos with
pure-Python equivalents that run inside Clawcross without external dependencies.

Two main capabilities:
  1. ArXiv search for academic mentor distillation (replaces supervisor/tools/arxiv-search.mjs)
  2. Feishu (Lark) message/doc collection for colleague distillation (replaces colleague-skill/tools/feishu_auto_collector.py)

Both are designed to feed into import_colleague_skill() / import_mentor_skill()
in team_creator_service.py.
"""

from __future__ import annotations

import json
import logging
import re
import time
import utils.scheduler_service
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# ArXiv Search — pure Python, no Node.js
# ──────────────────────────────────────────────────────────────

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class ArxivPaper:
    title: str = ""
    summary: str = ""
    authors: list[str] = field(default_factory=list)
    published: str = ""
    arxiv_id: str = ""
    year: int | None = None
    venue: str = "ArXiv"


def search_arxiv(
    author_name: str,
    *,
    max_results: int = 20,
    timeout: int = 30,
) -> list[ArxivPaper]:
    """Search ArXiv for papers by a given author.

    Uses the ArXiv API (Atom XML format), parsed with stdlib xml.etree.
    No external dependencies required.
    """
    query = f"au:{author_name}"
    params = urllib.parse.urlencode({
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    url = f"{ARXIV_API_URL}?{params}"

    _log.info("ArXiv search: %s (max_results=%d)", author_name, max_results)

    xml_bytes = b""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Clawcross/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                xml_bytes = resp.read()
            # ArXiv returns plain text "Rate exceeded." on rate limit
            if b"Rate exceeded" in xml_bytes:
                wait = 4 * (attempt + 1)
                _log.info("ArXiv rate limit, waiting %ds (attempt %d/3)", wait, attempt + 1)
                time.sleep(wait)
                xml_bytes = b""
                continue
            break
        except (urllib.error.URLError, OSError) as exc:
            _log.warning("ArXiv search attempt %d failed for '%s': %s", attempt + 1, author_name, exc)
            if attempt < 2:
                time.sleep(3)
            else:
                return []

    if not xml_bytes or b"Rate exceeded" in xml_bytes:
        _log.warning("ArXiv search exhausted retries for '%s'", author_name)
        return []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        _log.warning("ArXiv XML parse error: %s", exc)
        return []

    papers: list[ArxivPaper] = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        title_el = entry.find("atom:title", ARXIV_NS)
        summary_el = entry.find("atom:summary", ARXIV_NS)
        published_el = entry.find("atom:published", ARXIV_NS)
        id_el = entry.find("atom:id", ARXIV_NS)

        title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
        if not title:
            continue

        summary = (summary_el.text or "").strip().replace("\n", " ") if summary_el is not None else ""
        published = (published_el.text or "").strip() if published_el is not None else ""
        arxiv_id = (id_el.text or "").strip().rsplit("/", 1)[-1] if id_el is not None else ""

        year: int | None = None
        if published:
            try:
                year = datetime.fromisoformat(published.replace("Z", "+00:00")).year
            except (ValueError, TypeError):
                pass

        authors = []
        for author_el in entry.findall("atom:author", ARXIV_NS):
            name_el = author_el.find("atom:name", ARXIV_NS)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        papers.append(ArxivPaper(
            title=title,
            summary=summary[:500],
            authors=authors,
            published=published,
            arxiv_id=arxiv_id,
            year=year,
        ))

    _log.info("ArXiv found %d papers for '%s'", len(papers), author_name)
    return papers


def arxiv_papers_to_mentor_json(
    papers: list[ArxivPaper],
    mentor_name: str,
    affiliation: str = "",
) -> dict[str, Any]:
    """Convert ArXiv search results into a supervisor-compatible mentor JSON.

    This builds a minimal {name}.json that can be fed directly into
    import_mentor_skill() in team_creator_service.py.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Extract research fields from titles
    primary_fields: list[str] = []
    field_patterns = {
        "knowledge graph": re.compile(r"knowledge.*graph|graph.*knowledge", re.I),
        "natural language processing": re.compile(r"nlp|natural language|language model|text|sentiment", re.I),
        "computer vision": re.compile(r"computer vision|visual|image|object detection|segmentation", re.I),
        "machine learning": re.compile(r"machine learning|deep learning|neural network", re.I),
        "reinforcement learning": re.compile(r"reinforcement.*learn|policy.*gradient|reward", re.I),
        "graph neural networks": re.compile(r"graph.*neural|gnn|graph.*convolution", re.I),
        "large language models": re.compile(r"large language|llm|gpt|transformer|pretrain", re.I),
        "representation learning": re.compile(r"representation.*learn|embedding|contrastive", re.I),
        "information retrieval": re.compile(r"information retrieval|search|ranking|recommendation", re.I),
        "speech and audio": re.compile(r"speech|audio|asr|tts|voice", re.I),
    }

    field_counts: dict[str, int] = {}
    for paper in papers:
        combined = f"{paper.title} {paper.summary}"
        for field_name, pattern in field_patterns.items():
            if pattern.search(combined):
                field_counts[field_name] = field_counts.get(field_name, 0) + 1

    primary_fields = sorted(field_counts, key=lambda k: -field_counts[k])[:5]
    if not primary_fields:
        primary_fields = ["AI research"]

    # Build key publications
    key_publications = []
    for paper in papers[:10]:
        key_publications.append({
            "title": paper.title,
            "venue": paper.venue,
            "year": paper.year,
            "authors": ", ".join(paper.authors[:5]),
            "summary": paper.summary[:200],
        })

    # Determine research style
    has_theory = any(
        re.search(r"theor|proof|analysis|bound|complexity", p.title, re.I)
        for p in papers
    )
    has_applied = any(
        re.search(r"system|framework|application|deploy|tool", p.title, re.I)
        for p in papers
    )
    if has_theory and has_applied:
        style_type = "混合型"
    elif has_theory:
        style_type = "理论驱动型"
    else:
        style_type = "应用驱动型"

    # Build research summary
    research_summary = (
        f"{mentor_name} 是"
        + (f"{affiliation}的" if affiliation else "")
        + f"研究人员，已发表 {len(papers)}+ 篇论文，"
        + f"主要研究方向包括{'、'.join(primary_fields)}。"
    )

    return {
        "meta": {
            "version": "1.0",
            "created_at": now,
            "updated_at": now,
            "mentor_name": mentor_name,
            "affiliation": affiliation or "Unknown",
        },
        "profile": {
            "name_zh": mentor_name if any("\u4e00" <= c <= "\u9fff" for c in mentor_name) else "",
            "name_en": mentor_name if not any("\u4e00" <= c <= "\u9fff" for c in mentor_name) else "",
            "institution": affiliation,
            "department": "",
            "position": "",
            "website": "",
            "languages": ["zh", "en"] if any("\u4e00" <= c <= "\u9fff" for c in mentor_name) else ["en"],
        },
        "research": {
            "primary_fields": primary_fields,
            "secondary_fields": [],
            "research_summary": research_summary,
            "key_publications": key_publications,
            "recent_arxiv": [
                {"title": p.title, "year": p.year, "arxiv_id": p.arxiv_id}
                for p in papers[:5]
            ],
        },
        "style": {
            "research_style": {
                "type": style_type,
                "description": f"基于 {len(papers)} 篇论文的自动分析",
                "keywords": primary_fields[:6],
            },
            "communication_style": {
                "tone": "专业、严谨",
                "language": "中英双语" if any("\u4e00" <= c <= "\u9fff" for c in mentor_name) else "English",
                "characteristics": "基于论文分析的默认风格，建议补充更多材料",
            },
            "academic_values": ["学术严谨", "创新思维"],
            "expertise_areas": primary_fields[:6],
        },
        "achievements": {
            "honors": [],
            "academic_service": [],
            "citations": "N/A",
            "publications_count": f"{len(papers)}+ papers",
        },
        "source_materials": {
            "papers_count": len(papers),
            "websites_visited": [],
            "user_uploads": [],
        },
    }


# ──────────────────────────────────────────────────────────────
# Feishu (Lark) API — pure Python, minimal implementation
# ──────────────────────────────────────────────────────────────

FEISHU_BASE_URL = "https://open.feishu.cn/open-apis"
_feishu_token_cache: dict[str, Any] = {}


def _feishu_get_tenant_token(app_id: str, app_secret: str) -> str:
    """Get tenant_access_token from Feishu, with simple caching."""
    now = time.time()
    cache_key = f"{app_id}:{app_secret}"
    cached = _feishu_token_cache.get(cache_key)
    if cached and cached.get("expire", 0) > now + 60:
        return cached["token"]

    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = urllib.request.Request(
        f"{FEISHU_BASE_URL}/auth/v3/tenant_access_token/internal",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(f"Failed to get Feishu tenant token: {exc}") from exc

    if data.get("code") != 0:
        raise RuntimeError(f"Feishu auth failed: {data.get('msg', data)}")

    token = data["tenant_access_token"]
    _feishu_token_cache[cache_key] = {
        "token": token,
        "expire": now + data.get("expire", 7200),
    }
    return token


def _feishu_api_get(path: str, params: dict, token: str) -> dict:
    """Make a GET request to Feishu Open API."""
    qs = urllib.parse.urlencode(params) if params else ""
    url = f"{FEISHU_BASE_URL}{path}" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Read the error response body for better diagnostics
        try:
            error_body = json.loads(exc.read())
            _log.warning("Feishu API GET %s HTTP %d: %s", path, exc.code, error_body.get("msg", ""))
            return error_body
        except Exception:
            _log.warning("Feishu API GET %s HTTP %d", path, exc.code)
            return {"code": exc.code, "msg": f"HTTP {exc.code}"}
    except Exception as exc:
        _log.warning("Feishu API GET %s failed: %s", path, exc)
        return {"code": -1, "msg": str(exc)}


def feishu_collect_user_messages(
    app_id: str,
    app_secret: str,
    target_name: str,
    *,
    msg_limit: int = 500,
) -> str:
    """Collect messages from a Feishu user across shared group chats.

    This is a simplified Python version of the colleague-skill feishu_auto_collector.
    Returns formatted text suitable for colleague persona analysis.

    Requires the Feishu app to have:
      - im:message:readonly
      - im:chat:readonly
      - im:chat.members:readonly
    """
    token = _feishu_get_tenant_token(app_id, app_secret)

    # Step 1: Get all chats the bot is in
    chats: list[dict] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        data = _feishu_api_get("/im/v1/chats", params, token)
        if data.get("code") != 0:
            _log.warning("Failed to list chats: %s", data.get("msg"))
            break
        items = data.get("data", {}).get("items", [])
        chats.extend(items)
        if not data.get("data", {}).get("has_more"):
            break
        page_token = data.get("data", {}).get("page_token", "")

    if not chats:
        return f"# 飞书消息记录\n\n未找到 Bot 所在的群聊。请确认 Bot 已被添加到相关群。\n"

    # Step 2: Collect messages from each chat, filtering by sender name
    all_messages: list[dict] = []
    for chat in chats[:20]:  # Limit to 20 chats
        chat_id = chat.get("chat_id", "")
        chat_name = chat.get("name", chat_id)
        if not chat_id:
            continue

        msg_page_token = ""
        chat_msgs = 0
        while chat_msgs < msg_limit // max(len(chats), 1):
            params = {
                "container_id_type": "chat",
                "container_id": chat_id,
                "page_size": 50,
                "sort_type": "ByCreateTimeDesc",
            }
            if msg_page_token:
                params["page_token"] = msg_page_token

            data = _feishu_api_get("/im/v1/messages", params, token)
            if data.get("code") != 0:
                break

            items = data.get("data", {}).get("items", [])
            if not items:
                break

            for item in items:
                sender = item.get("sender", {})
                sender_name = sender.get("sender_type_name", "") or sender.get("id", "")

                # Parse message content
                body = item.get("body", {})
                content_raw = body.get("content", "")
                try:
                    content_obj = json.loads(content_raw)
                    if isinstance(content_obj, dict):
                        text_parts = []
                        for line in content_obj.get("content", []):
                            if isinstance(line, list):
                                for seg in line:
                                    if isinstance(seg, dict) and seg.get("tag") in ("text", "a"):
                                        text_parts.append(seg.get("text", ""))
                        content = " ".join(text_parts)
                    else:
                        content = str(content_obj)
                except (json.JSONDecodeError, TypeError):
                    content = content_raw

                content = content.strip()
                if not content or content in ("[图片]", "[文件]", "[表情]", "[语音]"):
                    continue

                # Filter by target name (loose match)
                if target_name and target_name not in str(sender_name):
                    continue

                ts = item.get("create_time", "")
                if ts:
                    try:
                        ts = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d %H:%M")
                    except (ValueError, OSError):
                        pass

                all_messages.append({
                    "content": content,
                    "time": ts,
                    "chat": chat_name,
                })
                chat_msgs += 1

            if not data.get("data", {}).get("has_more"):
                break
            msg_page_token = data.get("data", {}).get("page_token", "")

    if not all_messages:
        return f"# 飞书消息记录\n\n未找到 {target_name} 的消息。\n"

    # Format output
    long_msgs = [m for m in all_messages if len(m.get("content", "")) > 50]
    short_msgs = [m for m in all_messages if len(m.get("content", "")) <= 50]

    lines = [
        "# 飞书消息记录（Clawcross 自动采集）",
        f"目标：{target_name}",
        f"共 {len(all_messages)} 条消息",
        "",
        "---",
        "",
        "## 长消息（观点/决策/技术类）",
        "",
    ]
    for m in long_msgs:
        lines.append(f"[{m.get('time', '')}][{m.get('chat', '')}] {m['content']}")
        lines.append("")

    lines += ["---", "", "## 日常消息（风格参考）", ""]
    for m in short_msgs[:300]:
        lines.append(f"[{m.get('time', '')}] {m['content']}")

    return "\n".join(lines)


def feishu_messages_to_colleague_meta(
    target_name: str,
    messages_text: str = "",
    *,
    company: str = "",
    role: str = "",
    level: str = "",
    gender: str = "",
    mbti: str = "",
    personality_tags: list[str] | None = None,
    culture_tags: list[str] | None = None,
    impression: str = "",
) -> dict[str, Any]:
    """Build a colleague-skill compatible meta.json from Feishu-collected data.

    This meta.json can be fed directly into import_colleague_skill()
    in team_creator_service.py (together with an LLM-generated persona.md).
    """
    now = datetime.now(timezone.utc).isoformat()
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", target_name.lower()).strip("_") or "colleague"

    return {
        "name": target_name,
        "slug": slug,
        "created_at": now,
        "updated_at": now,
        "version": "v1",
        "profile": {
            "company": company,
            "level": level,
            "role": role,
            "gender": gender,
            "mbti": mbti,
        },
        "tags": {
            "personality": personality_tags or [],
            "culture": culture_tags or [],
        },
        "impression": impression,
        "knowledge_sources": ["feishu_auto_collect"] if messages_text else [],
        "corrections_count": 0,
    }
