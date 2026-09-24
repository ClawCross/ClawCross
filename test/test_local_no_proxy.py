"""Loopback traffic must not be handed to the user's HTTP proxy.

Desktop proxies export HTTP_PROXY plus a no_proxy written in shell-glob form
("127.*"). HTTP clients match no_proxy entries as literal hosts or domain
suffixes, so that entry matches nothing and every service-to-service call on
127.0.0.1 goes to the proxy, which answers 502.
"""

import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from utils.local_no_proxy import LOCAL_NO_PROXY_HOSTS, ensure_localhost_no_proxy

PROXY_VARS = ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY")


class LocalNoProxyTests(unittest.TestCase):
    def setUp(self):
        self._saved = {name: os.environ.get(name) for name in PROXY_VARS}
        for name in PROXY_VARS:
            os.environ.pop(name, None)
        os.environ["http_proxy"] = os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_glob_style_no_proxy_does_not_protect_loopback(self):
        # The situation as a proxy tool leaves it — this is the bug, not the fix.
        import requests

        os.environ["no_proxy"] = os.environ["NO_PROXY"] = "172.2*,127.*,localhost"
        self.assertFalse(
            requests.utils.should_bypass_proxies("http://127.0.0.1:51202/experts", no_proxy=None)
        )

    def test_loopback_bypasses_the_proxy_after_normalising(self):
        import urllib.request

        import requests

        os.environ["no_proxy"] = os.environ["NO_PROXY"] = "172.2*,127.*,localhost"
        ensure_localhost_no_proxy()

        self.assertTrue(
            requests.utils.should_bypass_proxies("http://127.0.0.1:51200/v1/models", no_proxy=None)
        )
        self.assertTrue(urllib.request.proxy_bypass("127.0.0.1"))

    def test_existing_entries_are_kept(self):
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = "example.com,172.2*"
        value = ensure_localhost_no_proxy()
        self.assertIn("example.com", value.split(","))
        self.assertIn("172.2*", value.split(","))
        for host in LOCAL_NO_PROXY_HOSTS:
            self.assertIn(host, value.split(","))

    def test_both_spellings_are_written(self):
        os.environ.pop("no_proxy", None)
        os.environ.pop("NO_PROXY", None)
        value = ensure_localhost_no_proxy()
        self.assertEqual(os.environ["no_proxy"], value)
        self.assertEqual(os.environ["NO_PROXY"], value)

    def test_running_twice_does_not_duplicate(self):
        os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.*"
        first = ensure_localhost_no_proxy()
        second = ensure_localhost_no_proxy()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
