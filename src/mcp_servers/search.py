import sys as _sys
import os as _os

_src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

import asyncio
import html
import ipaddress
import json
import logging
import re
from urllib.parse import urlparse

import httpx
from ddgs import DDGS
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("WebSearcher")

logging.getLogger("ddgs").setLevel(logging.WARNING)

DEFAULT_REGION = _os.getenv("WEB_SEARCH_REGION", "us-en")
DEFAULT_SAFESEARCH = _os.getenv("WEB_SEARCH_SAFESEARCH", "moderate")
DEFAULT_BACKEND = _os.getenv("WEB_SEARCH_BACKEND", "auto")
DEFAULT_PROVIDER = _os.getenv("WEB_SEARCH_PROVIDER", "auto")
DEFAULT_TIMEOUT = float(_os.getenv("WEB_SEARCH_TIMEOUT", "15"))
DEFAULT_BROWSER_ENGINE = _os.getenv("WEB_SEARCH_BROWSER_ENGINE", "duckduckgo")
try:
    DEFAULT_BROWSER_TIMEOUT = int(
        float(_os.getenv("WEB_SEARCH_BROWSER_TIMEOUT", str(DEFAULT_TIMEOUT)))
    )
except ValueError:
    DEFAULT_BROWSER_TIMEOUT = int(DEFAULT_TIMEOUT)
DEFAULT_NODE_BIN = _os.getenv("WEB_SEARCH_NODE_BIN", "node")
PROJECT_ROOT = _os.path.dirname(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
)
BROWSER_RUNNER = _os.path.join(PROJECT_ROOT, "scripts", "browser_search_runner.mjs")
MAX_RESULTS = 25
MAX_FETCH_CHARS = 50000

_VALID_SAFESEARCH = {"on", "moderate", "off"}
_VALID_FRESHNESS = {"", "d", "w", "m", "y"}
_VALID_PROVIDERS = {"auto", "ddgs", "browser"}
_VALID_BROWSER_ENGINES = {"duckduckgo", "bing"}
_HTML_TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style|noscript|svg|canvas).*?</\1>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _clean_query(query: str) -> str:
    query = (query or "").strip()
    if not query:
        raise ValueError("query must not be empty")
    return query


def _clamp_int(value: int, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        parsed = minimum
    return min(parsed, maximum)


def _normalize_safesearch(value: str) -> str:
    value = (value or DEFAULT_SAFESEARCH or "moderate").strip().lower()
    return value if value in _VALID_SAFESEARCH else "moderate"


def _normalize_freshness(value: str) -> str:
    value = (value or "").strip().lower()
    if value in {"day", "daily", "today"}:
        return "d"
    if value in {"week", "weekly"}:
        return "w"
    if value in {"month", "monthly"}:
        return "m"
    if value in {"year", "yearly"}:
        return "y"
    return value if value in _VALID_FRESHNESS else ""


def _normalize_provider(value: str, *, allow_auto: bool = True) -> str:
    value = (value or DEFAULT_PROVIDER or "auto").strip().lower()
    aliases = {
        "duck": "ddgs",
        "duckduckgo": "ddgs",
        "duckduckgo-search": "ddgs",
        "http": "ddgs",
        "playwright": "browser",
        "local_browser": "browser",
        "local-browser": "browser",
    }
    value = aliases.get(value, value)
    if value == "auto" and allow_auto:
        return "auto"
    if value in _VALID_PROVIDERS - {"auto"}:
        return value
    return "auto" if allow_auto else "ddgs"


def _normalize_browser_engine(value: str) -> str:
    value = (value or DEFAULT_BROWSER_ENGINE or "duckduckgo").strip().lower()
    aliases = {
        "ddg": "duckduckgo",
        "duck": "duckduckgo",
        "duckduckgo.com": "duckduckgo",
        "bing.com": "bing",
    }
    value = aliases.get(value, value)
    return value if value in _VALID_BROWSER_ENGINES else "duckduckgo"


def _normalize_fetch_provider(value: str) -> str:
    value = (value or DEFAULT_PROVIDER or "auto").strip().lower()
    aliases = {
        "direct": "http",
        "direct_http": "http",
        "direct-http": "http",
        "ddgs": "http",
        "playwright": "browser",
        "local_browser": "browser",
        "local-browser": "browser",
    }
    value = aliases.get(value, value)
    if value in {"auto", "http", "browser"}:
        return value
    return "auto"


def _split_csv(value: str) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,;\n]+", value)
    return [p.strip() for p in parts if p.strip()]


def _normalize_domain(raw: str) -> str:
    raw = (raw or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or raw).strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _normalize_domains(value: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in _split_csv(value):
        domain = _normalize_domain(item)
        if domain and domain not in seen:
            seen.add(domain)
            out.append(domain)
    return out


def _result_url(item: dict) -> str:
    return str(item.get("href") or item.get("url") or item.get("link") or "").strip()


def _host_from_url(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").strip(".").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _domain_matches(host: str, domain: str) -> bool:
    host = _normalize_domain(host)
    domain = _normalize_domain(domain)
    return bool(host and domain and (host == domain or host.endswith("." + domain)))


def _append_domain_operators(query: str, include_domains: list[str], exclude_domains: list[str]) -> str:
    rewritten = query
    for domain in include_domains:
        rewritten += f" site:{domain}"
    for domain in exclude_domains:
        rewritten += f" -site:{domain}"
    return rewritten


def _normalize_search_result(item: dict, *, rank: int, kind: str) -> dict:
    url = _result_url(item)
    return {
        "rank": rank,
        "kind": kind,
        "title": str(item.get("title") or "无标题"),
        "url": url,
        "domain": _host_from_url(url),
        "snippet": str(item.get("body") or item.get("snippet") or ""),
        "source": str(item.get("source") or item.get("publisher") or ""),
        "published_at": str(item.get("date") or item.get("published") or ""),
        "raw": item,
    }


def _dedupe_and_filter(
    raw_results: list[dict],
    *,
    kind: str,
    include_domains: list[str],
    exclude_domains: list[str],
    max_results: int,
) -> list[dict]:
    normalized: list[dict] = []
    seen_urls: set[str] = set()
    for item in raw_results:
        url = _result_url(item)
        if not url or url in seen_urls:
            continue
        host = _host_from_url(url)
        if include_domains and not any(_domain_matches(host, d) for d in include_domains):
            continue
        if exclude_domains and any(_domain_matches(host, d) for d in exclude_domains):
            continue
        seen_urls.add(url)
        normalized.append(_normalize_search_result(item, rank=len(normalized) + 1, kind=kind))
        if len(normalized) >= max_results:
            break
    return normalized


def _format_search_markdown(payload: dict, *, empty_icon: str, title_icon: str) -> str:
    query = payload.get("query", "")
    if not payload.get("ok"):
        return f"⚠️ 搜索失败: {payload.get('error', 'unknown error')}"
    results = payload.get("results") or []
    if not results:
        return f"{empty_icon} 未找到与 \"{query}\" 相关的结果。"

    output = f"{title_icon} 搜索 \"{query}\" 的结果：\n\n"
    for result in results:
        title = result.get("title") or "无标题"
        snippet = result.get("snippet") or "无摘要"
        url = result.get("url") or ""
        source = result.get("source") or result.get("domain") or ""
        published_at = result.get("published_at") or ""
        meta = ""
        if source or published_at:
            bits = [x for x in (source, published_at) if x]
            meta = " (" + " · ".join(bits) + ")"
        output += f"{result.get('rank')}. **{title}**{meta}\n   {snippet}\n   链接: {url}\n\n"
    return output


def _build_search_payload(
    *,
    query: str,
    kind: str,
    max_results: int = 8,
    region: str = DEFAULT_REGION,
    safesearch: str = DEFAULT_SAFESEARCH,
    freshness: str = "",
    backend: str = DEFAULT_BACKEND,
    include_domains: str = "",
    exclude_domains: str = "",
) -> dict:
    try:
        clean_query = _clean_query(query)
        safe_max = _clamp_int(max_results, default=8, minimum=1, maximum=MAX_RESULTS)
        include = _normalize_domains(include_domains)
        exclude = _normalize_domains(exclude_domains)
        freshness_value = _normalize_freshness(freshness)
        rewritten_query = _append_domain_operators(clean_query, include, exclude)
        search_kwargs = {
            "region": (region or DEFAULT_REGION).strip() or DEFAULT_REGION,
            "safesearch": _normalize_safesearch(safesearch),
            "max_results": safe_max * 2 if include or exclude else safe_max,
            "backend": (backend or DEFAULT_BACKEND).strip() or DEFAULT_BACKEND,
        }
        if freshness_value:
            search_kwargs["timelimit"] = freshness_value

        with DDGS(timeout=DEFAULT_TIMEOUT) as ddgs:
            if kind == "news":
                raw_results = list(ddgs.news(rewritten_query, **search_kwargs))
            else:
                raw_results = list(ddgs.text(rewritten_query, **search_kwargs))

        results = _dedupe_and_filter(
            raw_results,
            kind=kind,
            include_domains=include,
            exclude_domains=exclude,
            max_results=safe_max,
        )
        return {
            "ok": True,
            "provider": "ddgs",
            "kind": kind,
            "query": clean_query,
            "rewritten_query": rewritten_query,
            "result_count": len(results),
            "filters": {
                "region": search_kwargs["region"],
                "safesearch": search_kwargs["safesearch"],
                "freshness": freshness_value or None,
                "backend": search_kwargs["backend"],
                "include_domains": include,
                "exclude_domains": exclude,
            },
            "results": results,
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "ddgs",
            "kind": kind,
            "query": query,
            "error": str(exc),
            "results": [],
        }


def _attempt_summary(payload: dict) -> dict:
    summary = {
        "ok": bool(payload.get("ok")),
        "provider": payload.get("provider"),
        "result_count": payload.get("result_count"),
    }
    if payload.get("error"):
        summary["error"] = payload.get("error")
    if payload.get("status_code"):
        summary["status_code"] = payload.get("status_code")
    if payload.get("chars") is not None:
        summary["chars"] = payload.get("chars")
    return summary


async def _run_browser_runner(payload: dict, *, timeout: int) -> dict:
    if not _os.path.exists(BROWSER_RUNNER):
        return {
            "ok": False,
            "provider": "browser",
            "error": f"browser runner not found: {BROWSER_RUNNER}",
        }

    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        proc = await asyncio.create_subprocess_exec(
            DEFAULT_NODE_BIN,
            BROWSER_RUNNER,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "provider": "browser",
            "error": f"node executable not found: {DEFAULT_NODE_BIN}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "browser",
            "error": str(exc),
        }

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(encoded), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "ok": False,
            "provider": "browser",
            "error": f"browser runner timed out after {timeout}s",
        }

    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        return {
            "ok": False,
            "provider": "browser",
            "error": stderr_text or f"browser runner exited with code {proc.returncode}",
        }
    if not stdout_text:
        return {
            "ok": False,
            "provider": "browser",
            "error": stderr_text or "browser runner returned empty output",
        }

    try:
        data = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "provider": "browser",
            "error": f"browser runner returned invalid JSON: {exc}",
        }
    if not isinstance(data, dict):
        return {
            "ok": False,
            "provider": "browser",
            "error": "browser runner returned non-object JSON",
        }
    data.setdefault("provider", "browser")
    if stderr_text and not data.get("stderr"):
        data["stderr"] = stderr_text[-1000:]
    return data


async def _build_browser_search_payload(
    *,
    query: str,
    kind: str,
    max_results: int = 8,
    freshness: str = "",
    include_domains: str = "",
    exclude_domains: str = "",
    browser_engine: str = DEFAULT_BROWSER_ENGINE,
) -> dict:
    try:
        clean_query = _clean_query(query)
        safe_max = _clamp_int(max_results, default=8, minimum=1, maximum=MAX_RESULTS)
        include = _normalize_domains(include_domains)
        exclude = _normalize_domains(exclude_domains)
        freshness_value = _normalize_freshness(freshness)
        browser_query = clean_query
        if kind == "news" and "news" not in browser_query.lower():
            browser_query = f"{browser_query} news"
        rewritten_query = _append_domain_operators(browser_query, include, exclude)
        engine = _normalize_browser_engine(browser_engine)
        safe_timeout = _clamp_int(
            DEFAULT_BROWSER_TIMEOUT,
            default=int(DEFAULT_TIMEOUT),
            minimum=5,
            maximum=90,
        )
        runner_payload = {
            "mode": "search",
            "query": rewritten_query,
            "engine": engine,
            "maxResults": safe_max * 2 if include or exclude else safe_max,
            "timeoutMs": safe_timeout * 1000,
        }
        runner_result = await _run_browser_runner(runner_payload, timeout=safe_timeout + 5)
        if not runner_result.get("ok"):
            return {
                "ok": False,
                "provider": "browser",
                "engine": engine,
                "kind": kind,
                "query": clean_query,
                "rewritten_query": rewritten_query,
                "error": runner_result.get("error") or "browser search failed",
                "results": [],
            }

        raw_results = runner_result.get("raw_results") or runner_result.get("results") or []
        if not isinstance(raw_results, list):
            raw_results = []
        results = _dedupe_and_filter(
            raw_results,
            kind=kind,
            include_domains=include,
            exclude_domains=exclude,
            max_results=safe_max,
        )
        return {
            "ok": True,
            "provider": "browser",
            "engine": engine,
            "kind": kind,
            "query": clean_query,
            "rewritten_query": rewritten_query,
            "result_count": len(results),
            "filters": {
                "freshness": freshness_value or None,
                "include_domains": include,
                "exclude_domains": exclude,
            },
            "results": results,
            "browser": {
                "search_url": runner_result.get("search_url"),
                "final_url": runner_result.get("final_url"),
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "browser",
            "kind": kind,
            "query": query,
            "error": str(exc),
            "results": [],
        }


async def _build_search_provider_payload(
    *,
    query: str,
    kind: str,
    max_results: int = 8,
    region: str = DEFAULT_REGION,
    safesearch: str = DEFAULT_SAFESEARCH,
    freshness: str = "",
    backend: str = DEFAULT_BACKEND,
    include_domains: str = "",
    exclude_domains: str = "",
    provider: str = DEFAULT_PROVIDER,
    browser_engine: str = DEFAULT_BROWSER_ENGINE,
) -> dict:
    normalized_provider = _normalize_provider(provider)
    if normalized_provider == "browser":
        return await _build_browser_search_payload(
            query=query,
            kind=kind,
            max_results=max_results,
            freshness=freshness,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            browser_engine=browser_engine,
        )

    ddgs_payload = _build_search_payload(
        query=query,
        kind=kind,
        max_results=max_results,
        region=region,
        safesearch=safesearch,
        freshness=freshness,
        backend=backend,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
    )
    if normalized_provider == "ddgs":
        return ddgs_payload

    providers_tried = ["ddgs"]
    if ddgs_payload.get("ok") and ddgs_payload.get("results"):
        ddgs_payload["providers_tried"] = providers_tried
        return ddgs_payload

    browser_payload = await _build_browser_search_payload(
        query=query,
        kind=kind,
        max_results=max_results,
        freshness=freshness,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        browser_engine=browser_engine,
    )
    providers_tried.append("browser")
    if browser_payload.get("ok"):
        browser_payload["fallback_from"] = "ddgs"
        browser_payload["providers_tried"] = providers_tried
        browser_payload["previous_attempt"] = _attempt_summary(ddgs_payload)
        return browser_payload

    ddgs_payload["providers_tried"] = providers_tried
    ddgs_payload["browser_fallback"] = _attempt_summary(browser_payload)
    return ddgs_payload


def _is_blocked_fetch_host(host: str) -> bool:
    host = (host or "").strip().lower()
    if not host:
        return True
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved


def _clean_html_text(content: str) -> tuple[str, str]:
    title = ""
    title_match = _HTML_TITLE_RE.search(content)
    if title_match:
        title = html.unescape(_TAG_RE.sub("", title_match.group(1))).strip()
    content = _SCRIPT_STYLE_RE.sub("\n", content)
    content = re.sub(r"(?i)<br\s*/?>", "\n", content)
    content = re.sub(r"(?i)</(p|div|section|article|li|h[1-6]|tr)>", "\n", content)
    text = html.unescape(_TAG_RE.sub(" ", content))
    text = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    text = _BLANK_LINES_RE.sub("\n\n", text).strip()
    return title, text


async def _fetch_url_payload(url: str, *, max_chars: int = 12000, timeout: int = 15) -> dict:
    try:
        parsed = urlparse((url or "").strip())
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("only http and https URLs are supported")
        if _is_blocked_fetch_host(parsed.hostname or ""):
            raise ValueError("blocked private/local URL")
        safe_max_chars = _clamp_int(max_chars, default=12000, minimum=500, maximum=MAX_FETCH_CHARS)
        safe_timeout = _clamp_int(timeout, default=15, minimum=3, maximum=60)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) ClawCrossWebSearch/1.0 Safari/537.36"
            )
        }
        async with httpx.AsyncClient(
            timeout=safe_timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            resp = await client.get(url)
        content_type = resp.headers.get("content-type", "")
        text = resp.text
        title = ""
        if "html" in content_type.lower() or "<html" in text[:500].lower():
            title, text = _clean_html_text(text)
        else:
            text = text.strip()
        truncated = len(text) > safe_max_chars
        return {
            "ok": True,
            "provider": "http",
            "url": url,
            "final_url": str(resp.url),
            "status_code": resp.status_code,
            "content_type": content_type,
            "title": title,
            "text": text[:safe_max_chars],
            "truncated": truncated,
            "chars": min(len(text), safe_max_chars),
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "http",
            "url": url,
            "error": str(exc),
        }


async def _fetch_url_browser_payload(url: str, *, max_chars: int = 12000, timeout: int = 15) -> dict:
    try:
        parsed = urlparse((url or "").strip())
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("only http and https URLs are supported")
        if _is_blocked_fetch_host(parsed.hostname or ""):
            raise ValueError("blocked private/local URL")

        safe_max_chars = _clamp_int(max_chars, default=12000, minimum=500, maximum=MAX_FETCH_CHARS)
        safe_timeout = _clamp_int(timeout, default=15, minimum=3, maximum=90)
        runner_payload = {
            "mode": "fetch",
            "url": url,
            "maxChars": safe_max_chars,
            "timeoutMs": safe_timeout * 1000,
        }
        runner_result = await _run_browser_runner(runner_payload, timeout=safe_timeout + 5)
        if not runner_result.get("ok"):
            return {
                "ok": False,
                "provider": "browser",
                "url": url,
                "error": runner_result.get("error") or "browser fetch failed",
            }

        text = str(runner_result.get("text") or "")
        truncated = bool(runner_result.get("truncated"))
        return {
            "ok": True,
            "provider": "browser",
            "url": url,
            "final_url": str(runner_result.get("final_url") or url),
            "status_code": runner_result.get("status_code"),
            "content_type": str(runner_result.get("content_type") or ""),
            "title": str(runner_result.get("title") or ""),
            "text": text[:safe_max_chars],
            "truncated": truncated or len(text) > safe_max_chars,
            "chars": min(len(text), safe_max_chars),
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "browser",
            "url": url,
            "error": str(exc),
        }


async def _fetch_url_provider_payload(
    url: str,
    *,
    max_chars: int = 12000,
    timeout: int = 15,
    provider: str = DEFAULT_PROVIDER,
) -> dict:
    normalized_provider = _normalize_fetch_provider(provider)
    if normalized_provider == "browser":
        return await _fetch_url_browser_payload(url, max_chars=max_chars, timeout=timeout)

    http_payload = await _fetch_url_payload(url, max_chars=max_chars, timeout=timeout)
    if normalized_provider == "http":
        return http_payload

    text = str(http_payload.get("text") or "").strip()
    if http_payload.get("ok") and len(text) >= 200:
        http_payload["providers_tried"] = ["http"]
        return http_payload

    browser_payload = await _fetch_url_browser_payload(url, max_chars=max_chars, timeout=timeout)
    if browser_payload.get("ok"):
        browser_payload["fallback_from"] = "http"
        browser_payload["providers_tried"] = ["http", "browser"]
        browser_payload["previous_attempt"] = _attempt_summary(http_payload)
        return browser_payload

    http_payload["providers_tried"] = ["http", "browser"]
    http_payload["browser_fallback"] = _attempt_summary(browser_payload)
    return http_payload


@mcp.tool()
async def web_search(
    query: str,
    max_results: int = 5,
    region: str = DEFAULT_REGION,
    safesearch: str = DEFAULT_SAFESEARCH,
    freshness: str = "",
    include_domains: str = "",
    exclude_domains: str = "",
    provider: str = DEFAULT_PROVIDER,
    browser_engine: str = DEFAULT_BROWSER_ENGINE,
) -> str:
    """
    Search the web and return readable Markdown results.

    This is the backward-compatible tool for chat responses. Use
    web_search_json when downstream code needs stable fields.
    freshness supports d/w/m/y. include_domains and exclude_domains accept
    comma-separated domains.
    """
    payload = await _build_search_provider_payload(
        query=query,
        kind="web",
        max_results=min(_clamp_int(max_results, default=5, minimum=1, maximum=10), 10),
        region=region,
        safesearch=safesearch,
        freshness=freshness,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        provider=provider,
        browser_engine=browser_engine,
    )
    return _format_search_markdown(payload, empty_icon="🔍", title_icon="🔍")


@mcp.tool()
async def web_news(
    query: str,
    max_results: int = 5,
    region: str = DEFAULT_REGION,
    safesearch: str = DEFAULT_SAFESEARCH,
    freshness: str = "",
    include_domains: str = "",
    exclude_domains: str = "",
    provider: str = DEFAULT_PROVIDER,
    browser_engine: str = DEFAULT_BROWSER_ENGINE,
) -> str:
    """
    Search current news and return readable Markdown results.

    freshness supports d/w/m/y. include_domains and exclude_domains accept
    comma-separated domains.
    """
    payload = await _build_search_provider_payload(
        query=query,
        kind="news",
        max_results=min(_clamp_int(max_results, default=5, minimum=1, maximum=10), 10),
        region=region,
        safesearch=safesearch,
        freshness=freshness,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        provider=provider,
        browser_engine=browser_engine,
    )
    return _format_search_markdown(payload, empty_icon="📰", title_icon="📰")


@mcp.tool()
async def web_search_json(
    query: str,
    max_results: int = 8,
    region: str = DEFAULT_REGION,
    safesearch: str = DEFAULT_SAFESEARCH,
    freshness: str = "",
    backend: str = DEFAULT_BACKEND,
    include_domains: str = "",
    exclude_domains: str = "",
    provider: str = DEFAULT_PROVIDER,
    browser_engine: str = DEFAULT_BROWSER_ENGINE,
) -> str:
    """
    Search the web and return structured JSON.

    Result schema includes ok/provider/kind/query/filters/result_count/results.
    Each result has rank, title, url, domain, snippet, source, published_at.
    provider supports auto, ddgs, and browser. auto tries DDGS first and falls
    back to a local Playwright browser when the lightweight provider fails or
    returns no results.
    """
    return _json(
        await _build_search_provider_payload(
            query=query,
            kind="web",
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            freshness=freshness,
            backend=backend,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider=provider,
            browser_engine=browser_engine,
        )
    )


@mcp.tool()
async def web_news_json(
    query: str,
    max_results: int = 8,
    region: str = DEFAULT_REGION,
    safesearch: str = DEFAULT_SAFESEARCH,
    freshness: str = "",
    backend: str = DEFAULT_BACKEND,
    include_domains: str = "",
    exclude_domains: str = "",
    provider: str = DEFAULT_PROVIDER,
    browser_engine: str = DEFAULT_BROWSER_ENGINE,
) -> str:
    """
    Search news and return structured JSON.
    """
    return _json(
        await _build_search_provider_payload(
            query=query,
            kind="news",
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            freshness=freshness,
            backend=backend,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            provider=provider,
            browser_engine=browser_engine,
        )
    )


@mcp.tool()
async def web_fetch_url(
    url: str,
    max_chars: int = 12000,
    timeout: int = 15,
    provider: str = DEFAULT_PROVIDER,
) -> str:
    """
    Fetch a public web URL and return cleaned page text as JSON.

    Private/local IP literals and localhost are blocked. This tool is intended
    for public pages discovered by web_search_json/web_news_json. provider
    supports auto/http/browser; auto tries direct HTTP first, then browser
    rendering when direct fetch fails or produces too little text.
    """
    return _json(
        await _fetch_url_provider_payload(
            url,
            max_chars=max_chars,
            timeout=timeout,
            provider=provider,
        )
    )


@mcp.tool()
async def web_browser_search(
    query: str,
    max_results: int = 8,
    engine: str = DEFAULT_BROWSER_ENGINE,
    freshness: str = "",
    include_domains: str = "",
    exclude_domains: str = "",
) -> str:
    """
    Search through a local Playwright browser and return structured JSON.

    This tool needs local Node + Playwright Chromium. It does not require a
    paid API key or registration.
    """
    return _json(
        await _build_browser_search_payload(
            query=query,
            kind="web",
            max_results=max_results,
            freshness=freshness,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            browser_engine=engine,
        )
    )


@mcp.tool()
async def web_browser_fetch(url: str, max_chars: int = 12000, timeout: int = 15) -> str:
    """
    Render a public URL in a local Playwright browser and return page text.
    """
    return _json(await _fetch_url_browser_payload(url, max_chars=max_chars, timeout=timeout))


@mcp.tool()
async def web_research_brief(
    query: str,
    max_results: int = 6,
    fetch_top: int = 2,
    max_chars_per_page: int = 4000,
    region: str = DEFAULT_REGION,
    safesearch: str = DEFAULT_SAFESEARCH,
    freshness: str = "",
    include_domains: str = "",
    exclude_domains: str = "",
    provider: str = DEFAULT_PROVIDER,
    browser_engine: str = DEFAULT_BROWSER_ENGINE,
) -> str:
    """
    Research helper: structured search plus cleaned text from top results.

    Use this when an agent needs both search result metadata and lightweight
    page evidence. fetch_top is capped at 5 to avoid slow broad crawling.
    """
    payload = await _build_search_provider_payload(
        query=query,
        kind="web",
        max_results=max_results,
        region=region,
        safesearch=safesearch,
        freshness=freshness,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
        provider=provider,
        browser_engine=browser_engine,
    )
    if not payload.get("ok"):
        return _json(payload)

    safe_fetch_top = _clamp_int(fetch_top, default=2, minimum=0, maximum=5)
    pages: list[dict] = []
    for result in (payload.get("results") or [])[:safe_fetch_top]:
        page = await _fetch_url_provider_payload(
            result.get("url") or "",
            max_chars=max_chars_per_page,
            timeout=int(DEFAULT_TIMEOUT),
            provider=provider,
        )
        page["rank"] = result.get("rank")
        pages.append(page)

    payload["fetched_pages"] = pages
    return _json(payload)


if __name__ == "__main__":
    mcp.run()
