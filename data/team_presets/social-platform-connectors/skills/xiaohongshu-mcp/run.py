#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE = "http://localhost:18060"


def _json(data: object, status: int = 0) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return status


def _base() -> str:
    return os.getenv("XIAOHONGSHU_MCP_URL", DEFAULT_BASE).rstrip("/")


def request(method: str, path: str, body: str = "") -> dict:
    if not path.startswith("/"):
        path = "/" + path
    url = _base() + path
    data = body.encode() if body else None
    headers = {"User-Agent": "ClawCross-XiaohongshuWrapper/1.0"}
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode(errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
        return {"success": True, "status": resp.status, "response": parsed}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        return _json({
            "usage": [
                "./run.sh status",
                "./run.sh raw GET /health",
                "./run.sh raw POST /path '{\"json\":\"payload\"}'",
            ],
            "env": {"XIAOHONGSHU_MCP_URL": DEFAULT_BASE},
        })
    cmd = argv[1]
    try:
        if cmd == "status":
            checks = []
            for path in ("/health", "/status", "/"):
                try:
                    return _json({**request("GET", path), "checked_path": path, "base_url": _base()})
                except Exception as exc:
                    checks.append({"path": path, "error": str(exc)})
            return _json({
                "success": False,
                "base_url": _base(),
                "error": "xiaohongshu service did not respond on common status paths",
                "checks": checks,
            }, 1)
        if cmd == "raw":
            if len(argv) < 4:
                return _json({"success": False, "error": "usage: raw METHOD PATH [JSON_BODY]"}, 2)
            return _json(request(argv[2], argv[3], argv[4] if len(argv) > 4 else ""))
        return _json({"success": False, "error": f"unknown command: {cmd}"}, 2)
    except urllib.error.URLError as exc:
        return _json({
            "success": False,
            "base_url": _base(),
            "error": str(exc),
            "hint": "start the Xiaohongshu MCP service first or set XIAOHONGSHU_MCP_URL",
        }, 1)
    except Exception as exc:
        return _json({"success": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
