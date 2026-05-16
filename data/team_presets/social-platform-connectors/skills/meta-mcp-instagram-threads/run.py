#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request


GRAPH = "https://graph.facebook.com/v25.0"


def _json(data: object, status: int = 0) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return status


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required env var: {name}")
    return value


def _get(path: str, params: dict[str, str]) -> dict:
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _post(path: str, params: dict[str, str]) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{GRAPH}/{path}", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"success": True, "response": json.loads(resp.read().decode())}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
        return {"success": False, "status": exc.code, "error": parsed}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        return _json({
            "usage": [
                "./run.sh check-instagram",
                "./run.sh check-threads",
                "./run.sh threads-dry-run 'text'",
                "./run.sh threads-post 'text'",
            ],
            "env": [
                "INSTAGRAM_ACCESS_TOKEN",
                "INSTAGRAM_USER_ID",
                "THREADS_ACCESS_TOKEN",
                "THREADS_USER_ID",
            ],
        })
    cmd = argv[1]
    try:
        if cmd == "check-instagram":
            data = _get(_env("INSTAGRAM_USER_ID"), {
                "fields": "id,username,account_type,media_count",
                "access_token": _env("INSTAGRAM_ACCESS_TOKEN"),
            })
            return _json({"success": True, "response": data})
        if cmd == "check-threads":
            data = _get(_env("THREADS_USER_ID"), {
                "fields": "id,username,threads_profile_picture_url",
                "access_token": _env("THREADS_ACCESS_TOKEN"),
            })
            return _json({"success": True, "response": data})
        if cmd in {"threads-dry-run", "threads-post"}:
            text = " ".join(argv[2:]).strip()
            if not text:
                return _json({"success": False, "error": "text is required"}, 2)
            missing = [
                name for name in ("THREADS_ACCESS_TOKEN", "THREADS_USER_ID")
                if not os.getenv(name, "").strip()
            ]
            if cmd == "threads-dry-run":
                return _json({
                    "success": True,
                    "mode": "dry-run",
                    "missing_env": missing,
                    "text": text,
                    "ready_to_post": not missing,
                })
            if missing:
                return _json({"success": False, "missing_env": missing}, 2)
            created = _post(f"{_env('THREADS_USER_ID')}/threads", {
                "media_type": "TEXT",
                "text": text,
                "access_token": _env("THREADS_ACCESS_TOKEN"),
            })
            if not created.get("success"):
                return _json(created, 1)
            creation_id = str(created["response"]["id"])
            published = _post(f"{_env('THREADS_USER_ID')}/threads_publish", {
                "creation_id": creation_id,
                "access_token": _env("THREADS_ACCESS_TOKEN"),
            })
            return _json(published, 0 if published.get("success") else 1)
        return _json({"success": False, "error": f"unknown command: {cmd}"}, 2)
    except RuntimeError as exc:
        return _json({"success": False, "error": str(exc)}, 2)
    except Exception as exc:
        return _json({"success": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
