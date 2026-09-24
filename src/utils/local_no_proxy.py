"""Keep this machine's service-to-service HTTP off the user's HTTP proxy.

ClawCross services talk to each other over 127.0.0.1. A desktop proxy (clash,
v2ray, ...) exports HTTP_PROXY/HTTPS_PROXY and usually writes a companion
no_proxy in shell-glob form — ``127.*,172.2*,10.*,localhost``. curl, urllib
and requests match no_proxy entries as literal hosts or domain suffixes, so
``127.*`` matches nothing: every loopback request is handed to the proxy,
which cannot reach the port and answers 502.

That breaks startup rather than slowing it. The launcher's health probe and
run.sh's readiness curl both get 502 forever, the launcher hits its timeout
and tears down services that had in fact started, and run.sh then waits out
its own loop before reporting that nothing is listening.

Normalising no_proxy once per process fixes every HTTP client in it at the
same time — there are dozens of call sites across the services — and leaves
proxying for genuine external hosts untouched.
"""

from __future__ import annotations

import os

#: Loopback names every service uses to reach its siblings.
LOCAL_NO_PROXY_HOSTS = ("localhost", "127.0.0.1", "::1")


def ensure_localhost_no_proxy() -> str:
    """Prepend loopback hosts to no_proxy in a form HTTP clients actually match.

    Existing entries are kept and order is otherwise preserved. Both spellings
    of the variable are written, since clients differ on which they read.
    Returns the resulting value.
    """
    existing = os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or ""
    entries = [part.strip() for part in existing.split(",") if part.strip()]
    missing = [host for host in LOCAL_NO_PROXY_HOSTS if host not in entries]
    value = ",".join(missing + entries)
    os.environ["no_proxy"] = value
    os.environ["NO_PROXY"] = value
    return value
