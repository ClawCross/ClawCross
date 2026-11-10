"""Manually refresh the checked-in model catalog; never run during startup."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import urlopen

SOURCE = "https://catalog.openclaw.ai/models/v2/catalog.json"
FIELDS = ("id", "provider", "input", "reasoning", "contextWindow", "maxTokens",
          "thinkingLevelMap", "compat", "mediaInput", "pricing")


def compact_catalog(raw: dict) -> dict:
    return {
        "source": SOURCE, "license": "MIT", "schemaVersion": raw["schemaVersion"],
        "generatedAt": raw["generatedAt"], "sourceCommit": raw["sourceCommit"],
        "models": [{key: row[key] for key in FIELDS if key in row} for row in raw["models"]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Use a downloaded snapshot instead of fetching")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2] / "src/backend/common/model_catalog.json")
    args = parser.parse_args()
    if args.input:
        raw = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        with urlopen(SOURCE, timeout=30) as response:
            raw = json.load(response)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(compact_catalog(raw), ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {len(raw['models'])} models to {args.output}")


if __name__ == "__main__":
    main()
