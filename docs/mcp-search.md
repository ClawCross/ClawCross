# MCP Web Search

ClawCross exposes web search through `src/mcp_servers/search.py`.

The MCP server is started by the main agent as `search_service` and provides
three unified research tools.

The default provider mode is `auto`: ClawCross first tries the lightweight
DDGS path, then falls back to a local Playwright browser when the search fails,
returns no results, or a direct URL fetch produces too little text. This keeps
the feature free and registration-free while still supporting JS-rendered pages.

## Tools

| Tool | Use For | Return |
|---|---|---|
| `web_search` | Search the web or news; pick markdown or JSON | Markdown or JSON string |
| `web_fetch` | Fetch cleaned text from a public URL | JSON string |
| `web_research_brief` | Search plus cleaned text from top results | JSON string |

### `web_search`

```
web_search(
    query,
    kind="web"|"news",
    format="markdown"|"json",
    max_results=5,
    region, safesearch, freshness,
    include_domains, exclude_domains,
    provider="auto"|"ddgs"|"browser",
    browser_engine="duckduckgo"|"bing",
    backend,
)
```

- `kind="news"` switches the underlying provider call to news search; the
  markdown variant uses a 📰 icon.
- `format="markdown"` (default) caps `max_results` at 10 for chat readability.
  `format="json"` caps at 25.
- `provider="browser"` forces the local Playwright runner (no DDGS attempt).

### `web_fetch`

```
web_fetch(url, max_chars=12000, timeout=15, provider="auto"|"http"|"browser")
```

`provider="auto"` does a direct HTTP fetch first, then renders the page in
the local Playwright browser if the direct fetch fails or returns too little
text. Private/local hosts are always blocked (see Fetch Safety below).

### `web_research_brief`

Runs `web_search(format="json")` and then fetches cleaned text from the top
`fetch_top` results (capped at 5).

## Structured Result Shape

`web_search(format="json")` returns:

```json
{
  "ok": true,
  "provider": "ddgs",
  "kind": "web",
  "query": "clawcross oasis",
  "rewritten_query": "clawcross oasis site:github.com",
  "result_count": 1,
  "filters": {
    "region": "us-en",
    "safesearch": "moderate",
    "freshness": "w",
    "backend": "auto",
    "include_domains": ["github.com"],
    "exclude_domains": []
  },
  "results": [
    {
      "rank": 1,
      "kind": "web",
      "title": "Example",
      "url": "https://example.com",
      "domain": "example.com",
      "snippet": "Result summary",
      "source": "",
      "published_at": "",
      "raw": {}
    }
  ]
}
```

On failure, `ok` is `false` and `error` contains the provider error.

When `provider=auto` falls back to the browser, `provider` becomes `browser`
and the payload includes `fallback_from`, `providers_tried`, and a compact
`previous_attempt` summary.

## Filters

Common parameters:

- `max_results`: capped at `25` for `format="json"` and `10` for
  `format="markdown"`.
- `region`: DDGS region code. Defaults to `WEB_SEARCH_REGION` or `us-en`.
- `safesearch`: `on`, `moderate`, or `off`.
- `freshness`: `d`, `w`, `m`, or `y`.
- `include_domains`: comma-separated domains. The query is rewritten with `site:`.
- `exclude_domains`: comma-separated domains. The query is rewritten with `-site:`.
- `backend`: DDGS backend selector. Defaults to `WEB_SEARCH_BACKEND` or `auto`.
- `provider`: `auto`, `ddgs`, or `browser`. `auto` is the recommended default.
- `browser_engine`: `duckduckgo` or `bing` for browser-backed search.

## Local Browser Provider

`provider=browser` (for both `web_search` and `web_fetch`) uses
`scripts/browser_search_runner.mjs`. The runner opens a headless Chromium
browser through Playwright, extracts visible search result links or page text,
and returns JSON to the Python MCP server.

This path does not require a paid API key or registration. It does require local
Node dependencies and a Playwright browser install. If Chromium is missing, run:

```bash
npx playwright install chromium
```

Use cases:

- Search engines block or rate-limit the lightweight DDGS request path.
- A page needs JavaScript rendering before useful text appears.
- You want a closer approximation of what a local browser sees.

Tradeoffs:

- Browser search is slower and heavier than DDGS.
- Search engine pages can still show bot checks or consent screens.
- Browser mode is isolated/headless, but it should still be limited to public
  web pages.

## Fetch Safety

`web_fetch` is for public web pages discovered by search. It blocks:

- non-HTTP(S) schemes
- localhost
- literal private, loopback, link-local, multicast, and reserved IPs
- `.local` hosts

It returns cleaned text without scripts/styles, a detected title, final URL,
status code, content type, and truncation metadata.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `WEB_SEARCH_REGION` | `us-en` | Default DDGS region |
| `WEB_SEARCH_SAFESEARCH` | `moderate` | Default safesearch mode |
| `WEB_SEARCH_BACKEND` | `auto` | Default DDGS backend |
| `WEB_SEARCH_PROVIDER` | `auto` | Provider mode: `auto`, `ddgs`, or `browser` |
| `WEB_SEARCH_TIMEOUT` | `15` | Provider timeout in seconds |
| `WEB_SEARCH_BROWSER_ENGINE` | `duckduckgo` | Browser search engine: `duckduckgo` or `bing` |
| `WEB_SEARCH_BROWSER_TIMEOUT` | `15` | Browser runner timeout in seconds |
| `WEB_SEARCH_NODE_BIN` | `node` | Node executable used for the browser runner |
