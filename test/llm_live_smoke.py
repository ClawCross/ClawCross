"""
Opt-in live smoke test for a real LLM provider.

Resolution order for config:
1. CLI flags
2. LLM_LIVE_* environment variables
3. Clawcross config/.env LLM_* values
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from services.llm_factory import create_chat_model, extract_text, infer_provider


def _read_env_file() -> dict[str, str]:
    env_path = PROJECT_ROOT / "config" / ".env"
    if not env_path.exists():
        return {}

    data: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _pick(cli_value: str, env_key: str, file_key: str, saved: dict[str, str]) -> str:
    value = (cli_value or "").strip()
    if value:
        return value
    value = os.getenv(env_key, "").strip()
    if value:
        return value
    return (saved.get(file_key) or "").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a real LLM smoke test against the configured provider.")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--prompt", default=os.getenv("LLM_LIVE_PROMPT", "Reply with exactly LIVE_OK and nothing else."))
    parser.add_argument("--expect", default=os.getenv("LLM_LIVE_EXPECT", "LIVE_OK"))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("LLM_LIVE_TIMEOUT", "60")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    saved = _read_env_file()

    api_key = _pick(args.api_key, "LLM_LIVE_API_KEY", "LLM_API_KEY", saved)
    base_url = _pick(args.base_url, "LLM_LIVE_BASE_URL", "LLM_BASE_URL", saved)
    model = _pick(args.model, "LLM_LIVE_MODEL", "LLM_MODEL", saved)
    provider = _pick(args.provider, "LLM_LIVE_PROVIDER", "LLM_PROVIDER", saved)
    resolved_provider = infer_provider(
        model=model,
        base_url=base_url,
        provider=provider,
        api_key=api_key,
    )

    missing = [
        name
        for name, value in (
            ("base_url", base_url),
            ("model", model),
        )
        if not value
    ]
    if not api_key and resolved_provider != "ollama":
        missing.append("api_key")
    if missing:
        print("LLM live smoke test failed: missing " + ", ".join(missing))
        return 1

    if not api_key and resolved_provider == "ollama":
        api_key = "ollama"

    chat = create_chat_model(
        model=model,
        api_key=api_key,
        base_url=base_url,
        provider=provider,
        temperature=0,
        max_tokens=64,
        timeout=args.timeout,
        max_retries=1,
    )

    response = chat.invoke([HumanMessage(content=args.prompt)])
    text = extract_text(response.content).strip()
    if not text:
        print("LLM live smoke test failed: empty response.")
        return 1

    print(f"provider={resolved_provider}")
    print(f"model={model}")
    print(f"base_url={base_url}")
    print(f"response={text[:200]}")

    expected = (args.expect or "").strip()
    if expected and expected.upper() not in text.upper():
        print(f"LLM live smoke test failed: expected marker {expected!r} not found.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
