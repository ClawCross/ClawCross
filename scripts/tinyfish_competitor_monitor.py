#!/usr/bin/env python3
"""CLI wrapper for the shared TinyFish monitor service."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from services.tinyfish_monitor_service import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
