"""The backend modules import from their root (src/backend); the frontend is the
``frontend`` package under src/."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
for _path in (str(_SRC), str(_SRC / "backend")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
