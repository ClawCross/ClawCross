"""Small interactive client for the current /v1/chat/completions API."""

import argparse
import os
from pathlib import Path
import sys

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.backend.common.runtime_paths import ENV_FILE


def send_message(base_url: str, token: str, user: str, session: str, text: str) -> str:
    response = requests.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}:{user}"},
        json={"model": "webot", "session_id": session, "stream": False,
              "session_mode": "chat", "messages": [{"role": "user", "content": text}]},
        timeout=180,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"] or ""


def main() -> int:
    load_dotenv(ENV_FILE)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default="default")
    parser.add_argument("--session", default="api-example")
    parser.add_argument("--base-url", default=f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}")
    parser.add_argument("--message", help="Send one message instead of starting an interactive loop.")
    args = parser.parse_args()
    token = os.getenv("INTERNAL_TOKEN", "")
    if not token:
        parser.error("INTERNAL_TOKEN is required; configure the runtime or set the environment variable.")
    try:
        if args.message:
            print(send_message(args.base_url, token, args.user, args.session, args.message))
            return 0
        while True:
            message = input("You (exit to quit): ").strip()
            if message.lower() == "exit":
                return 0
            if message:
                print(send_message(args.base_url, token, args.user, args.session, message))
    except (EOFError, KeyboardInterrupt):
        return 0
    except requests.RequestException as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
