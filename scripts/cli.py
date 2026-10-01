#!/usr/bin/env python3
"""Compatibility launcher for commands already negotiated with external agents.

The CLI implementation lives in src/cli; this entry contains no application logic.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.cli.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
