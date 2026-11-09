import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import front


if __name__ == "__main__":
    port = int(os.environ.get("CLAWCROSS_BROWSER_PORT", "51219"))
    front.app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
