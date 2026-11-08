import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"  # the frontend package; it puts src/backend on the path itself
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import frontend.server as front
from flask import render_template, request
import re


@front.app.before_request
def disable_test_service_worker():
    # A worker's fetch bypasses page.route API fixtures. Refuse registration on
    # this isolated server; Playwright's injected blocker itself throws inside
    # opaque sandboxed app frames, obscuring genuine app errors.
    if request.path == '/sw.js':
        return '', 404


@front.app.route('/__test__/studio')
def studio_components():
    """Exercise the Studio renderer independently of the unified app shell.

    This route only exists on the isolated test server. End-to-end shell and
    embedded bridge behavior is covered by conversation-workspace.spec.js.
    """
    html = render_template('index.html')
    return re.sub(r'<script src="/static/js/studio-embedded\.js[^\"]*"></script>', '', html)


if __name__ == "__main__":
    port = int(os.environ.get("CLAWCROSS_BROWSER_PORT", "51219"))
    front.app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
