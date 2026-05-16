#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.parse
import urllib.request


POST_URL = "https://api.twitter.com/2/tweets"
MAX_TWEET_CHARS = 280


def _json(data: object, status: int = 0) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return status


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required env var: {name}")
    return value


def _percent(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _oauth_header(method: str, url: str, body_params: dict[str, str]) -> str:
    api_key = _env("X_API_KEY")
    api_secret = _env("X_API_SECRET")
    access_token = _env("X_ACCESS_TOKEN")
    access_secret = _env("X_ACCESS_SECRET")

    oauth_params = {
        "oauth_consumer_key": api_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }
    all_params = {**oauth_params, **body_params}
    param_string = "&".join(
        f"{_percent(k)}={_percent(v)}" for k, v in sorted(all_params.items())
    )
    base = "&".join([method.upper(), _percent(url), _percent(param_string)])
    signing_key = f"{_percent(api_secret)}&{_percent(access_secret)}".encode()
    signature = base64.b64encode(
        hmac.new(signing_key, base.encode(), hashlib.sha1).digest()
    ).decode()
    oauth_params["oauth_signature"] = signature
    return "OAuth " + ", ".join(
        f'{_percent(k)}="{_percent(v)}"' for k, v in sorted(oauth_params.items())
    )


def _post_tweet(text: str) -> dict:
    payload = json.dumps({"text": text}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        POST_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": _oauth_header("POST", POST_URL, {}),
            "Content-Type": "application/json",
            "User-Agent": "ClawCross-XPublisher/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return {"success": True, "status": resp.status, "response": data}
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
                "./run.sh dry-run 'tweet text'",
                "./run.sh post 'tweet text'",
            ],
            "required_env": [
                "X_API_KEY",
                "X_API_SECRET",
                "X_ACCESS_TOKEN",
                "X_ACCESS_SECRET",
            ],
        })
    command = argv[1]
    text = " ".join(argv[2:]).strip()
    if not text:
        return _json({"success": False, "error": "tweet text is required"}, 2)
    length = len(text)
    if length > MAX_TWEET_CHARS:
        return _json({
            "success": False,
            "error": "tweet text exceeds 280 characters",
            "length": length,
        }, 2)
    if command == "dry-run":
        missing = [
            name for name in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
            if not os.getenv(name, "").strip()
        ]
        return _json({
            "success": True,
            "mode": "dry-run",
            "length": length,
            "missing_env": missing,
            "text": text,
            "ready_to_post": not missing,
        })
    if command == "post":
        try:
            return _json(_post_tweet(text), 0)
        except RuntimeError as exc:
            return _json({"success": False, "error": str(exc)}, 2)
    return _json({"success": False, "error": f"unknown command: {command}"}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
