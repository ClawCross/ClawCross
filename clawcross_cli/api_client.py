"""Self-contained API client for ClawCross display commands.

Mirrors helpers from ``scripts/cli.py`` without importing that module (which
has heavy side effects on import). All network calls degrade gracefully —
errors come back as ``{"error": "..."}`` so callers can render friendly
messages instead of crashing.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Make sure the project root is importable so we can pull runtime paths.
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from src.utils.runtime_paths import DATA_DIR, USER_FILES_DIR  # type: ignore
except Exception:  # pragma: no cover - runtime fallback
    DATA_DIR = Path(os.getenv("CLAWCROSS_DATA_DIR", str(Path.home() / ".clawcross" / "data")))
    USER_FILES_DIR = Path(
        os.getenv("CLAWCROSS_USER_FILES_DIR", str(Path.home() / ".clawcross" / "user_files"))
    )


# ── Constants / config ──────────────────────────────────────────────────────

PORT_AGENT = int(os.getenv("PORT_AGENT", "51200"))
PORT_OASIS = int(os.getenv("PORT_OASIS", "51202"))
PORT_FRONTEND = int(os.getenv("PORT_FRONTEND", "51209"))
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")

AGENT_BASE = f"http://127.0.0.1:{PORT_AGENT}"
OASIS_BASE = f"http://127.0.0.1:{PORT_OASIS}"
FRONT_BASE = f"http://127.0.0.1:{PORT_FRONTEND}"

def _canonical_user() -> str:
    """Resolve the canonical CLI user.

    Priority: CLAW_USER / CLI_USER env > first user in users.json > first
    sub-directory of USER_FILES_DIR with content > "admin".
    """
    for var in ("CLAW_USER", "CLI_USER"):
        v = (os.getenv(var) or "").strip()
        if v:
            return v
    users_json = Path(
        os.getenv("CLAWCROSS_HOME", str(Path.home() / ".clawcross"))
    ) / "config" / "users.json"
    if users_json.is_file():
        try:
            data = json.loads(users_json.read_text("utf-8"))
            if isinstance(data, dict) and data:
                return next(iter(data))
        except Exception:
            pass
    if USER_FILES_DIR.is_dir():
        for child in sorted(USER_FILES_DIR.iterdir()):
            if child.is_dir() and any(child.iterdir()):
                return child.name
    return "admin"


DEFAULT_USER = _canonical_user()


# ── HTTP helpers (copied verbatim style from scripts/cli.py) ─────────────────

def _req(method: str, url: str, headers: dict | None = None,
         data: dict | list | None = None, params: dict | None = None,
         timeout: int = 30) -> tuple[int, Any]:
    """Send an HTTP request and return ``(status_code, body)``.

    The body is JSON-decoded when the response has a JSON content type.
    Network/decoding errors return ``(0, {"error": "..."})`` so callers can
    render a friendly message instead of crashing.
    """
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body_bytes = None
    if data is not None:
        body_bytes = json.dumps(data).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body_bytes, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            raw = resp.read()
            if "json" in ct:
                try:
                    return resp.status, json.loads(raw)
                except Exception:
                    return resp.status, raw
            return resp.status, raw
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
        except Exception:
            err = {"error": e.reason}
        return e.code, err
    except (socket.timeout, TimeoutError):
        return 0, {"error": f"request timed out: {url}"}
    except urllib.error.URLError as e:
        return 0, {"error": f"connection failed: {e.reason}"}
    except Exception as e:  # pragma: no cover - defensive
        return 0, {"error": f"unexpected error: {e}"}


def _agent_headers() -> dict:
    return {"X-Internal-Token": INTERNAL_TOKEN}


def _front_headers(user: str | None = None) -> dict:
    h: dict[str, str] = {"X-Internal-Token": INTERNAL_TOKEN}
    uid = (user or DEFAULT_USER or "").strip()
    if uid:
        h["X-User-Id"] = uid
    return h


def backend_unreachable(body: Any) -> bool:
    if isinstance(body, dict):
        err = str(body.get("error") or "")
        return any(s in err.lower() for s in ("connection failed", "timed out", "refused"))
    return False


def friendly_error(url: str, code: int, body: Any) -> str:
    if code == 0 and isinstance(body, dict):
        return f"Backend not reachable at {url} ({body.get('error', 'unknown error')})"
    if isinstance(body, dict):
        return f"[{code}] {body.get('error') or body.get('message') or body}"
    return f"[{code}] {body}"


# ── Workflow filesystem helpers (mirrored from scripts/cli.py) ───────────────

def _workflow_yaml_dir(user_id: str, team: str = "") -> str:
    user_root = os.path.join(str(USER_FILES_DIR), user_id)
    if team:
        return os.path.join(user_root, "teams", team, "oasis", "yaml")
    return os.path.join(user_root, "oasis", "yaml")


def _workflow_python_dir(user_id: str, team: str = "") -> str:
    user_root = os.path.join(str(USER_FILES_DIR), user_id)
    if team:
        return os.path.join(user_root, "teams", team, "oasis", "python")
    return os.path.join(user_root, "oasis", "python")


def _iter_yaml_workflow_dirs(user_id: str, team: str = "") -> list[tuple[str, str, str]]:
    if not user_id:
        return []
    user_root = os.path.join(str(USER_FILES_DIR), user_id)
    if team:
        return [("team", team, _workflow_yaml_dir(user_id, team))]
    dirs: list[tuple[str, str, str]] = [("personal", "", _workflow_yaml_dir(user_id, ""))]
    teams_root = os.path.join(user_root, "teams")
    if os.path.isdir(teams_root):
        for team_name in sorted(os.listdir(teams_root)):
            team_dir = os.path.join(teams_root, team_name)
            if os.path.isdir(team_dir):
                dirs.append(("team", team_name, _workflow_yaml_dir(user_id, team_name)))
    return dirs


def _iter_python_workflow_dirs(user_id: str, team: str = "") -> list[tuple[str, str, str]]:
    if not user_id:
        return []
    user_root = os.path.join(str(USER_FILES_DIR), user_id)
    if team:
        return [("team", team, _workflow_python_dir(user_id, team))]
    dirs: list[tuple[str, str, str]] = [("personal", "", _workflow_python_dir(user_id, ""))]
    teams_root = os.path.join(user_root, "teams")
    if os.path.isdir(teams_root):
        for team_name in sorted(os.listdir(teams_root)):
            team_dir = os.path.join(teams_root, team_name)
            if os.path.isdir(team_dir):
                dirs.append(("team", team_name, _workflow_python_dir(user_id, team_name)))
    return dirs


def resolve_yaml_workflow_path(user_id: str, name: str, team: str = "") -> tuple[str | None, str | None]:
    """Return the absolute path to a YAML workflow (or an error message)."""
    if not name:
        return None, "no workflow name provided"
    target = name if name.endswith((".yaml", ".yml")) else f"{name}.yaml"
    matches = []
    for scope, team_name, yaml_dir in _iter_yaml_workflow_dirs(user_id, team):
        path = os.path.join(yaml_dir, target)
        if os.path.isfile(path):
            label = f"team:{team_name}" if scope == "team" else "personal"
            matches.append((label, path))
    if not matches:
        return None, f"YAML workflow not found: {target}"
    if len(matches) > 1:
        where = ", ".join(label for label, _ in matches)
        return None, f"multiple YAML workflows named {target} ({where}); specify --team"
    return matches[0][1], None


def resolve_python_workflow_path(user_id: str, name: str, team: str = "") -> tuple[str | None, str | None]:
    if not name:
        return None, "no workflow name provided"
    target = name if name.endswith(".py") else f"{name}.py"
    matches = []
    for scope, team_name, py_dir in _iter_python_workflow_dirs(user_id, team):
        path = os.path.join(py_dir, target)
        if os.path.isfile(path):
            label = f"team:{team_name}" if scope == "team" else "personal"
            matches.append((label, path))
    if not matches:
        return None, f"Python workflow not found: {target}"
    if len(matches) > 1:
        where = ", ".join(label for label, _ in matches)
        return None, f"multiple Python workflows named {target} ({where}); specify --team"
    return matches[0][1], None


# ── High-level fetchers ─────────────────────────────────────────────────────

def list_teams(user: str | None = None) -> tuple[list[dict], str | None]:
    url = f"{FRONT_BASE}/teams"
    code, body = _req("GET", url, headers=_front_headers(user))
    if code == 200:
        if isinstance(body, dict):
            teams = body.get("teams") or body.get("items") or []
        elif isinstance(body, list):
            teams = body
        else:
            teams = []
        return [t for t in teams if isinstance(t, dict) or isinstance(t, str)], None
    return [], friendly_error(url, code, body)


def team_members(name: str, user: str | None = None) -> tuple[dict | None, str | None]:
    url = f"{FRONT_BASE}/teams/{urllib.parse.quote(name, safe='')}/members"
    code, body = _req("GET", url, headers=_front_headers(user))
    if code == 200 and isinstance(body, dict):
        return body, None
    return None, friendly_error(url, code, body)


def team_experts(name: str, user: str | None = None) -> tuple[list[dict], str | None]:
    """GET /teams/<n>/experts — return the persona list for a team."""
    url = f"{FRONT_BASE}/teams/{urllib.parse.quote(name, safe='')}/experts"
    code, body = _req("GET", url, headers=_front_headers(user))
    if code != 200:
        return [], friendly_error(url, code, body)
    if isinstance(body, dict):
        items = body.get("experts") or body.get("personas") or body.get("items") or []
    elif isinstance(body, list):
        items = body
    else:
        items = []
    return [it for it in items if isinstance(it, dict)], None


def create_expert(
    team: str,
    *,
    name: str,
    tag: str,
    persona: str,
    temperature: float = 0.7,
    name_en: str = "",
    category: str = "",
    description: str = "",
    user: str | None = None,
) -> tuple[dict | None, str | None]:
    """POST /teams/<team>/experts — add a custom persona to a team."""
    url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/experts"
    payload: dict[str, Any] = {
        "name": name, "tag": tag, "persona": persona, "temperature": temperature,
    }
    for key, val in (("name_en", name_en), ("category", category), ("description", description)):
        if val:
            payload[key] = val
    code, body = _req("POST", url, headers=_front_headers(user), data=payload)
    if code == 200:
        return body if isinstance(body, dict) else {"ok": True}, None
    return None, friendly_error(url, code, body)


def update_expert(
    team: str,
    tag: str,
    *,
    name: str | None = None,
    persona: str | None = None,
    temperature: float | None = None,
    name_en: str | None = None,
    category: str | None = None,
    description: str | None = None,
    user: str | None = None,
) -> tuple[dict | None, str | None]:
    """PUT /teams/<team>/experts/<tag> — update fields of an existing persona.

    Only non-None fields are sent; the backend keeps prior values otherwise.
    """
    url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/experts/{urllib.parse.quote(tag, safe='')}"
    payload: dict[str, Any] = {}
    for key, val in (
        ("name", name), ("persona", persona), ("temperature", temperature),
        ("name_en", name_en), ("category", category), ("description", description),
    ):
        if val is not None:
            payload[key] = val
    code, body = _req("PUT", url, headers=_front_headers(user), data=payload)
    if code == 200:
        return body if isinstance(body, dict) else {"ok": True}, None
    return None, friendly_error(url, code, body)


def delete_expert(team: str, tag: str, user: str | None = None) -> tuple[bool, str | None]:
    """DELETE /teams/<team>/experts/<tag> — remove a persona by tag."""
    url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/experts/{urllib.parse.quote(tag, safe='')}"
    code, body = _req("DELETE", url, headers=_front_headers(user))
    if 200 <= code < 300:
        return True, None
    return False, friendly_error(url, code, body)


def create_team(name: str, user: str | None = None) -> tuple[dict | None, str | None]:
    """POST /teams to create a new team folder. Mirrors cli.py:cmd_teams[create]."""
    url = f"{FRONT_BASE}/teams"
    code, body = _req("POST", url, headers=_front_headers(user), data={"team": name})
    if code == 200:
        return body if isinstance(body, dict) else {"ok": True}, None
    return None, friendly_error(url, code, body)


def delete_team(name: str, user: str | None = None) -> tuple[bool, str | None]:
    """DELETE /teams/<name> — remove a team folder and its internal agents."""
    url = f"{FRONT_BASE}/teams/{urllib.parse.quote(name, safe='')}"
    code, body = _req("DELETE", url, headers=_front_headers(user))
    if 200 <= code < 300:
        return True, None
    return False, friendly_error(url, code, body)


def rename_team(old: str, new: str, user: str | None = None) -> tuple[dict | None, str | None]:
    """PATCH /teams/<old> — rename the team folder to *new*."""
    url = f"{FRONT_BASE}/teams/{urllib.parse.quote(old, safe='')}"
    code, body = _req("PATCH", url, headers=_front_headers(user), data={"new_name": new})
    if code == 200:
        return body if isinstance(body, dict) else {"ok": True}, None
    return None, friendly_error(url, code, body)


def add_external_member(
    team: str,
    *,
    name: str,
    global_name: str,
    platform: str,
    tag: str = "",
    api_url: str = "",
    api_key: str = "",
    model: str = "",
    is_primary: bool = False,
    user: str | None = None,
) -> tuple[dict | None, str | None]:
    """POST /teams/<team>/members/external — add an external agent member."""
    url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/members/external"
    payload: dict[str, Any] = {
        "name": name, "global_name": global_name, "platform": platform,
        "tag": tag, "api_url": api_url, "api_key": api_key, "model": model,
        "is_primary": is_primary,
    }
    code, body = _req("POST", url, headers=_front_headers(user), data=payload)
    if code == 200:
        return body if isinstance(body, dict) else {"ok": True}, None
    return None, friendly_error(url, code, body)


def update_external_member(
    team: str,
    global_name: str,
    *,
    fields: dict[str, Any],
    user: str | None = None,
) -> tuple[dict | None, str | None]:
    """PUT /teams/<team>/members/external — update an external agent.

    The agent is matched by its current *global_name*; *fields* carries the
    attributes to change (may include a new ``global_name``).
    """
    url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/members/external"
    payload = {"global_name": global_name, **fields}
    code, body = _req("PUT", url, headers=_front_headers(user), data=payload)
    if code == 200:
        return body if isinstance(body, dict) else {"ok": True}, None
    return None, friendly_error(url, code, body)


def delete_external_member(
    team: str, global_name: str, user: str | None = None,
) -> tuple[bool, str | None]:
    """DELETE /teams/<team>/members/external — remove an external agent by global_name."""
    url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/members/external"
    code, body = _req("DELETE", url, headers=_front_headers(user), data={"global_name": global_name})
    if 200 <= code < 300:
        return True, None
    return False, friendly_error(url, code, body)


def save_workflow(
    user: str,
    name: str,
    yaml_content: str,
    *,
    team: str = "",
    description: str = "",
) -> tuple[dict | None, str | None]:
    """POST OASIS /workflows to save a YAML workflow. Mirrors cli.py:cmd_workflows[save]."""
    url = f"{OASIS_BASE}/workflows"
    data = {
        "user_id": user,
        "name": name,
        "schedule_yaml": yaml_content,
        "description": description,
        "team": team,
    }
    code, body = _req("POST", url, data=data)
    if code == 200:
        return body if isinstance(body, dict) else {"ok": True}, None
    return None, friendly_error(url, code, body)


def _skill_url(name: str, team: str = "") -> str:
    if team:
        return f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/skills/{urllib.parse.quote(name, safe='')}"
    return f"{FRONT_BASE}/skills/{urllib.parse.quote(name, safe='')}"


def create_skill(
    name: str,
    content: str,
    *,
    team: str = "",
    category: str = "",
    user: str | None = None,
) -> tuple[dict | None, str | None]:
    """POST a new SKILL.md (fails 409 if it exists). Mirrors front.py POST /skills/<name>.

    Use update_skill() to overwrite an existing skill.
    """
    payload: dict[str, Any] = {"content": content}
    if category:
        payload["category"] = category
    url = _skill_url(name, team)
    code, body = _req("POST", url, headers=_front_headers(user), data=payload)
    if code == 200:
        return body if isinstance(body, dict) else {"ok": True}, None
    return None, friendly_error(url, code, body)


def update_skill(
    name: str,
    content: str,
    *,
    team: str = "",
    user: str | None = None,
) -> tuple[dict | None, str | None]:
    """PUT — overwrite an existing SKILL.md. Mirrors front.py PUT /skills/<name>."""
    url = _skill_url(name, team)
    code, body = _req("PUT", url, headers=_front_headers(user), data={"content": content})
    if code == 200:
        return body if isinstance(body, dict) else {"ok": True}, None
    return None, friendly_error(url, code, body)


def delete_skill(
    name: str,
    *,
    team: str = "",
    user: str | None = None,
) -> tuple[bool, str | None]:
    """DELETE a managed skill — team-scoped when *team* is given, else personal.

    Mirrors create_skill: ``/teams/<team>/skills/<name>`` vs ``/skills/<name>``.
    """
    if team:
        url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/skills/{urllib.parse.quote(name, safe='')}"
    else:
        url = f"{FRONT_BASE}/skills/{urllib.parse.quote(name, safe='')}"
    code, body = _req("DELETE", url, headers=_front_headers(user))
    if 200 <= code < 300:
        return True, None
    return False, friendly_error(url, code, body)


def get_skill_detail(
    name: str,
    *,
    team: str = "",
    user: str | None = None,
) -> tuple[dict | None, str | None]:
    """GET full SKILL.md detail. Team-scoped when *team* is given, else personal.

    Returns the ``skill`` dict (keys: name, description, content, body, ...).
    """
    if team:
        url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/skills/{urllib.parse.quote(name, safe='')}"
    else:
        url = f"{FRONT_BASE}/skills/{urllib.parse.quote(name, safe='')}"
    code, body = _req("GET", url, headers=_front_headers(user))
    if code == 200 and isinstance(body, dict):
        return body.get("skill") if isinstance(body.get("skill"), dict) else body, None
    return None, friendly_error(url, code, body)


def create_cron(
    team: str,
    *,
    target_name: str,
    text: str,
    schedule_type: str = "cron",
    cron_expr: str = "",
    run_at: str = "",
    target_type: str = "internal",
    user: str | None = None,
) -> tuple[dict | None, str | None]:
    """Create a cron / one-shot alarm.

    With *team* → ``POST /teams/<team>/alarms``. Without → ``POST /mobile_alarms``
    in the public scope (team defaults to ``__public__`` server-side).
    """
    payload = {
        "target_type": target_type,
        "target_name": target_name,
        "schedule_type": schedule_type,
        "cron": cron_expr,
        "run_at": run_at,
        "text": text,
    }
    if team:
        url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/alarms"
    else:
        url = f"{FRONT_BASE}/mobile_alarms"
    code, body = _req("POST", url, headers=_front_headers(user), data=payload)
    if code == 200:
        return body if isinstance(body, dict) else {"ok": True}, None
    return None, friendly_error(url, code, body)


def list_cron_targets(team: str = "", user: str | None = None) -> tuple[list[dict], str | None]:
    """GET schedulable targets for a scope via ``/mobile_alarms``.

    Returns target dicts like ``{target_type, target_name, label}``. Empty
    *team* → the public scope.
    """
    params = {"team": team} if team else {"team": "__public__"}
    url = f"{FRONT_BASE}/mobile_alarms"
    code, body = _req("GET", url, headers=_front_headers(user), params=params)
    if code == 200 and isinstance(body, dict):
        targets = body.get("targets") or []
        return [t for t in targets if isinstance(t, dict)], None
    return [], friendly_error(url, code, body)


def delete_cron(
    task_id: str,
    *,
    team: str | None = None,
    user: str | None = None,
) -> tuple[bool, str | None]:
    """DELETE a cron/alarm by task_id.

    With *team* uses ``/teams/<team>/alarms/<task_id>``. Without, falls back to
    ``/mobile_alarms/<task_id>``.
    """
    if team:
        url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/alarms/{urllib.parse.quote(task_id, safe='')}"
    else:
        url = f"{FRONT_BASE}/mobile_alarms/{urllib.parse.quote(task_id, safe='')}"
    code, body = _req("DELETE", url, headers=_front_headers(user))
    if 200 <= code < 300:
        return True, None
    return False, friendly_error(url, code, body)


def delete_workflow(user: str, name: str, team: str = "") -> tuple[str | None, str | None]:
    """Delete a workflow's local file (YAML then Python). Returns (path, error).

    OASIS exposes no DELETE route — workflows are plain files the CLI reads
    directly (see list_workflows / save_workflow), so removal is just the
    inverse on the same resolved path.
    """
    path, yerr = resolve_yaml_workflow_path(user, name, team)
    if not path:
        path, perr = resolve_python_workflow_path(user, name, team)
        if not path:
            # Prefer the ambiguity hint ("multiple ... specify --team") if present.
            for msg in (yerr, perr):
                if msg and "multiple" in msg:
                    return None, msg
            return None, yerr or perr or "workflow not found"
    try:
        os.remove(path)
    except OSError as e:
        return None, f"Failed to delete {path}: {e}"
    return path, None


def list_workflows(user: str, team: str = "") -> list[dict]:
    """Combine YAML + Python workflow listings from the local filesystem."""
    items: list[dict] = []
    for scope, team_name, yaml_dir in _iter_yaml_workflow_dirs(user, team):
        if not os.path.isdir(yaml_dir):
            continue
        for fname in sorted(os.listdir(yaml_dir)):
            if not fname.endswith((".yaml", ".yml")):
                continue
            desc = ""
            fpath = os.path.join(yaml_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    first = f.readline().strip()
                if first.startswith("#"):
                    desc = first.lstrip("# ").strip()
            except Exception:
                pass
            items.append({
                "kind": "yaml",
                "file": fname,
                "name": fname.rsplit(".", 1)[0],
                "description": desc,
                "scope": scope,
                "team": team_name,
                "path": fpath,
            })
    for scope, team_name, py_dir in _iter_python_workflow_dirs(user, team):
        if not os.path.isdir(py_dir):
            continue
        for fname in sorted(os.listdir(py_dir)):
            if not fname.endswith(".py"):
                continue
            preview = ""
            fpath = os.path.join(py_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    preview = f.readline().strip()
            except Exception:
                pass
            items.append({
                "kind": "python",
                "file": fname,
                "name": fname.rsplit(".", 1)[0],
                "description": preview[:120],
                "scope": scope,
                "team": team_name,
                "path": fpath,
            })
    items.sort(key=lambda it: (it["kind"], it["scope"], it["team"], it["file"]))
    return items


def read_workflow_file(path: str) -> tuple[str | None, str | None]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), None
    except Exception as e:
        return None, f"failed to read {path}: {e}"


def list_skills(team: str = "", user: str | None = None) -> tuple[Any, str | None]:
    """List user-level managed skills.

    Without *team*, returns the user's personal skills via ``GET /skills``.
    With *team*, returns ``{"team": [...], "personal": [...]}`` via
    ``GET /teams/<team>/skills``. The response is a dict
    ``{"skills": {"personal": [...], "team": [...]?}}``.
    """
    if team:
        url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/skills"
    else:
        url = f"{FRONT_BASE}/skills"
    code, body = _req("GET", url, headers=_front_headers(user), timeout=20)
    if code == 200:
        return body, None
    return None, friendly_error(url, code, body)


def list_crons(team: str | None = None, user: str | None = None) -> tuple[list[dict], str | None]:
    """List cron/alarm entries. Uses ``/teams/<t>/alarms`` for a specific team
    (works with X-Internal-Token + X-User-Id on localhost) and falls back to
    ``/mobile_alarms`` for the team-wide view.
    """
    if team:
        url = f"{FRONT_BASE}/teams/{urllib.parse.quote(team, safe='')}/alarms"
    else:
        url = f"{FRONT_BASE}/mobile_alarms"
    code, body = _req("GET", url, headers=_front_headers(user))
    if code == 200 and isinstance(body, dict):
        alarms = body.get("alarms") or []
        return alarms if isinstance(alarms, list) else [], None
    return [], friendly_error(url, code, body)


def run_workflow(user: str, name: str, team: str, question: str,
                 kind: str = "yaml") -> tuple[dict, str | None]:
    """Launch a saved workflow.

    YAML: POST ``{OASIS_BASE}/topics`` with the resolved schedule.
    Python: POST ``{FRONT_BASE}/proxy_visual/run-python-workflow`` to spawn the
    standalone runner (front.py:_spawn_standalone_python_workflow).

    Returns ``(body, error)``. On failure the body is empty and ``error`` is a
    friendly message.
    """
    if kind == "python":
        py_path, err = resolve_python_workflow_path(user, name, team)
        if err or not py_path:
            return {}, err or "python workflow not found"
        url = f"{FRONT_BASE}/proxy_visual/run-python-workflow"
        payload = {
            "python_file": py_path,
            "question": question,
            "team": team or "",
        }
        code, body = _req("POST", url, headers=_front_headers(user), data=payload, timeout=30)
        if code == 200 and isinstance(body, dict):
            return body, None
        return {}, friendly_error(url, code, body)

    if kind != "yaml":
        return {}, f"unsupported workflow kind: {kind!r}"
    yaml_path, err = resolve_yaml_workflow_path(user, name, team)
    if err:
        return {}, err
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            yaml_content = f.read()
    except Exception as e:
        return {}, f"failed to read workflow {yaml_path}: {e}"
    payload = {
        "user_id": user,
        "question": question,
        "team": team or "",
        "schedule_file": yaml_path,
        "schedule_yaml": yaml_content,
    }
    url = f"{OASIS_BASE}/topics"
    code, body = _req("POST", url, data=payload, timeout=30)
    if code == 200 and isinstance(body, dict):
        return body, None
    return {}, friendly_error(url, code, body)


def list_topics(user: str | None = None) -> tuple[list[dict], str | None]:
    """GET {OASIS}/topics — the user's OASIS discussion topics (workflow runs)."""
    user = (user or DEFAULT_USER or "").strip()
    url = f"{OASIS_BASE}/topics"
    code, body = _req("GET", url, params={"user_id": user})
    if code == 200:
        if isinstance(body, list):
            return [t for t in body if isinstance(t, dict)], None
        if isinstance(body, dict):
            items = body.get("topics") or body.get("items") or []
            return [t for t in items if isinstance(t, dict)], None
        return [], None
    return [], friendly_error(url, code, body)


def get_topic(topic_id: str, user: str | None = None) -> tuple[dict | None, str | None]:
    """GET {OASIS}/topics/<id> — full discussion detail (status, posts, conclusion)."""
    user = (user or DEFAULT_USER or "").strip()
    url = f"{OASIS_BASE}/topics/{urllib.parse.quote(topic_id, safe='')}"
    code, body = _req("GET", url, params={"user_id": user})
    if code == 200 and isinstance(body, dict):
        return body, None
    return None, friendly_error(url, code, body)
