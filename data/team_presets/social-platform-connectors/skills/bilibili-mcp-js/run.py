#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request


def _json(data: object, status: int = 0) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return status


def _get(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 ClawCross-BilibiliToolkit/1.0",
            "Referer": "https://www.bilibili.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def search(keyword: str) -> dict:
    params = urllib.parse.urlencode({
        "search_type": "video",
        "keyword": keyword,
        "page": "1",
    })
    return _get(f"https://api.bilibili.com/x/web-interface/search/type?{params}")


def popular() -> dict:
    return _get("https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1")


def video(identifier: str) -> dict:
    key = "aid" if identifier.lower().startswith("av") else "bvid"
    value = identifier[2:] if key == "aid" else identifier
    params = urllib.parse.urlencode({key: value})
    return _get(f"https://api.bilibili.com/x/web-interface/view?{params}")


def up(mid: str) -> dict:
    params = urllib.parse.urlencode({"mid": mid})
    return _get(f"https://api.bilibili.com/x/web-interface/card?{params}")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        return _json({
            "usage": [
                "./run.sh search '关键词'",
                "./run.sh popular",
                "./run.sh video BV1xx",
                "./run.sh up 123456",
            ],
        })
    cmd = argv[1]
    try:
        if cmd == "search":
            if len(argv) < 3:
                return _json({"success": False, "error": "keyword is required"}, 2)
            data = search(" ".join(argv[2:]))
        elif cmd == "popular":
            data = popular()
        elif cmd == "video":
            if len(argv) < 3:
                return _json({"success": False, "error": "BV/AV id is required"}, 2)
            data = video(argv[2])
        elif cmd == "up":
            if len(argv) < 3:
                return _json({"success": False, "error": "UP mid is required"}, 2)
            data = up(argv[2])
        else:
            return _json({"success": False, "error": f"unknown command: {cmd}"}, 2)
        return _json({"success": data.get("code") == 0, "response": data})
    except Exception as exc:
        return _json({"success": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
