import json
import unittest
from unittest import mock

from src.mcp_servers import search


class McpSearchHelpersTests(unittest.TestCase):
    def test_clamp_int_has_lower_and_upper_bounds(self):
        self.assertEqual(search._clamp_int(-5, default=3, minimum=1, maximum=10), 1)
        self.assertEqual(search._clamp_int(99, default=3, minimum=1, maximum=10), 10)
        self.assertEqual(search._clamp_int("bad", default=3, minimum=1, maximum=10), 3)

    def test_normalize_domains_accepts_urls_and_csv(self):
        domains = search._normalize_domains("https://www.example.com/path, docs.python.org")
        self.assertEqual(domains, ["example.com", "docs.python.org"])

    def test_dedupe_and_filter_results_by_domain(self):
        raw = [
            {"title": "A", "href": "https://example.com/a", "body": "one"},
            {"title": "B", "href": "https://blocked.test/b", "body": "two"},
            {"title": "A duplicate", "href": "https://example.com/a", "body": "dup"},
            {"title": "C", "href": "https://docs.python.org/3/", "body": "three"},
        ]
        results = search._dedupe_and_filter(
            raw,
            kind="web",
            include_domains=[],
            exclude_domains=["blocked.test"],
            max_results=10,
        )
        self.assertEqual([r["url"] for r in results], ["https://example.com/a", "https://docs.python.org/3/"])
        self.assertEqual([r["rank"] for r in results], [1, 2])

    def test_domain_operator_rewrite(self):
        rewritten = search._append_domain_operators(
            "python workflow",
            ["docs.python.org"],
            ["spam.example"],
        )
        self.assertEqual(rewritten, "python workflow site:docs.python.org -site:spam.example")

    def test_clean_html_text_extracts_title_and_body(self):
        title, text = search._clean_html_text(
            "<html><head><title>Example &amp; Test</title><style>.x{}</style></head>"
            "<body><script>alert(1)</script><h1>Hello</h1><p>World&nbsp;now</p></body></html>"
        )
        self.assertEqual(title, "Example & Test")
        self.assertIn("Hello", text)
        self.assertIn("World", text)
        self.assertNotIn("alert", text)

    def test_blocks_local_fetch_hosts(self):
        self.assertTrue(search._is_blocked_fetch_host("localhost"))
        self.assertTrue(search._is_blocked_fetch_host("127.0.0.1"))
        self.assertTrue(search._is_blocked_fetch_host("10.0.0.1"))
        self.assertFalse(search._is_blocked_fetch_host("example.com"))

    def test_json_response_is_valid_json(self):
        payload = search._json({"ok": True, "results": [{"title": "测试"}]})
        parsed = json.loads(payload)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["results"][0]["title"], "测试")


class McpSearchBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_search_payload_normalizes_runner_results(self):
        async def fake_runner(payload, *, timeout):
            self.assertEqual(payload["mode"], "search")
            self.assertEqual(payload["engine"], "duckduckgo")
            self.assertIn("site:example.com", payload["query"])
            self.assertGreaterEqual(timeout, 5)
            return {
                "ok": True,
                "provider": "browser",
                "engine": "duckduckgo",
                "search_url": "https://duckduckgo.com/?q=python",
                "final_url": "https://duckduckgo.com/?q=python",
                "raw_results": [
                    {"title": "Example", "href": "https://example.com/a", "body": "alpha"},
                    {"title": "Other", "href": "https://other.test/b", "body": "beta"},
                ],
            }

        with mock.patch.object(search, "_run_browser_runner", new=fake_runner):
            payload = await search._build_browser_search_payload(
                query="python",
                kind="web",
                max_results=5,
                include_domains="example.com",
                browser_engine="ddg",
            )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "browser")
        self.assertEqual(payload["engine"], "duckduckgo")
        self.assertEqual(payload["result_count"], 1)
        self.assertEqual(payload["results"][0]["url"], "https://example.com/a")

    async def test_auto_search_falls_back_to_browser_when_ddgs_fails(self):
        def fake_ddgs(**kwargs):
            return {
                "ok": False,
                "provider": "ddgs",
                "kind": kwargs["kind"],
                "query": kwargs["query"],
                "error": "ddgs unavailable",
                "results": [],
            }

        async def fake_browser(**kwargs):
            return {
                "ok": True,
                "provider": "browser",
                "kind": kwargs["kind"],
                "query": kwargs["query"],
                "result_count": 1,
                "results": [{"rank": 1, "title": "Fallback", "url": "https://example.com"}],
            }

        with mock.patch.object(search, "_build_search_payload", side_effect=fake_ddgs):
            with mock.patch.object(search, "_build_browser_search_payload", new=fake_browser):
                payload = await search._build_search_provider_payload(
                    query="fallback query",
                    kind="web",
                    provider="auto",
                )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "browser")
        self.assertEqual(payload["fallback_from"], "ddgs")
        self.assertEqual(payload["providers_tried"], ["ddgs", "browser"])
        self.assertEqual(payload["previous_attempt"]["error"], "ddgs unavailable")

    async def test_browser_fetch_blocks_local_url_before_runner(self):
        async def forbidden_runner(payload, *, timeout):
            raise AssertionError("runner should not be called for local URLs")

        with mock.patch.object(search, "_run_browser_runner", new=forbidden_runner):
            payload = await search._fetch_url_browser_payload("http://127.0.0.1:51202")

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["provider"], "browser")
        self.assertIn("blocked", payload["error"])

    async def test_auto_fetch_falls_back_to_browser_for_empty_http_text(self):
        async def fake_http(url, *, max_chars, timeout):
            return {
                "ok": True,
                "provider": "http",
                "url": url,
                "status_code": 200,
                "text": "",
                "chars": 0,
            }

        async def fake_browser(url, *, max_chars, timeout):
            return {
                "ok": True,
                "provider": "browser",
                "url": url,
                "status_code": 200,
                "text": "rendered browser text",
                "chars": 21,
            }

        with mock.patch.object(search, "_fetch_url_payload", new=fake_http):
            with mock.patch.object(search, "_fetch_url_browser_payload", new=fake_browser):
                payload = await search._fetch_url_provider_payload(
                    "https://example.com",
                    provider="auto",
                )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "browser")
        self.assertEqual(payload["fallback_from"], "http")
        self.assertEqual(payload["providers_tried"], ["http", "browser"])


if __name__ == "__main__":
    unittest.main()
