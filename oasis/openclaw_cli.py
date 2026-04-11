import json
import os
import subprocess
from typing import Optional

from src.utils.logging_utils import get_logger

logger = get_logger("oasis.openclaw_cli")

# OpenClaw 2026.x schema: agents.list[].tools.profile 仅允许下列取值
ALLOWED_TOOLS_PROFILES = frozenset({"minimal", "coding", "messaging", "full"})
DEFAULT_TOOLS_PROFILE = "coding"


def sanitize_tools_dict(tools: Optional[dict]) -> dict:
    """将 tools.profile 规范为 CLI 允许的值，避免整份 openclaw.json 校验失败（删除/添加 agent 都会失败）。"""
    if not isinstance(tools, dict):
        return {}
    out = dict(tools)
    prof = out.get("profile")
    if prof is None:
        return out
    s = str(prof).strip()
    if s in ALLOWED_TOOLS_PROFILES:
        return out
    low = s.lower()
    aliases = {
        "code": "coding",
        "default": "coding",
        "dev": "coding",
        "developer": "coding",
    }
    out["profile"] = aliases.get(low, DEFAULT_TOOLS_PROFILE)
    return out


def sanitize_openclaw_root_agents_tools_profiles(root: dict) -> int:
    """就地修正 root['agents']['list'][*].tools.profile，返回修正的 agent 条目数。"""
    if not isinstance(root, dict):
        return 0
    agents = root.get("agents")
    if not isinstance(agents, dict):
        return 0
    lst = agents.get("list")
    if not isinstance(lst, list):
        return 0
    fixed = 0
    for entry in lst:
        if not isinstance(entry, dict):
            continue
        tools = entry.get("tools")
        if not isinstance(tools, dict):
            continue
        if "profile" not in tools:
            continue
        old = tools.get("profile")
        old_s = str(old).strip() if old is not None else ""
        if old_s in ALLOWED_TOOLS_PROFILES:
            continue
        entry["tools"] = sanitize_tools_dict(tools)
        fixed += 1
    return fixed


def openclaw_root_config_path() -> str:
    """Resolve OpenClaw root config path.

    Priority:
    1) OPENCLAW_CONFIG_FILE (explicit file path)
    2) OPENCLAW_HOME + /openclaw.json
    3) default ~/.openclaw/openclaw.json
    """
    env_file = (os.getenv("OPENCLAW_CONFIG_FILE", "") or "").strip()
    if env_file:
        return os.path.expanduser(env_file)

    env_home = (os.getenv("OPENCLAW_HOME", "") or "").strip()
    if env_home:
        return os.path.join(os.path.expanduser(env_home), "openclaw.json")

    return os.path.expanduser("~/.openclaw/openclaw.json")


def load_openclaw_root_config() -> Optional[dict]:
    """读取完整 openclaw.json（失败返回 None）。"""
    path = openclaw_root_config_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("load openclaw.json failed: %s", e)
        return None


def save_openclaw_root_config(data: dict) -> bool:
    """写回完整 openclaw.json（原子替换）。"""
    path = openclaw_root_config_path()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.warning("save openclaw.json failed: %s", e)
        return False


def _agents_subtree_from_root(root: Optional[dict]) -> Optional[dict]:
    if not root or not isinstance(root.get("agents"), dict):
        return None
    return root["agents"]


def _parse_first_json_document(raw: str):
    if not raw:
        return None
    idx = raw.find("{")
    arr_idx = raw.find("[")
    if idx < 0 or (arr_idx >= 0 and arr_idx < idx):
        idx = arr_idx
    if idx < 0:
        return None
    try:
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(raw[idx:])
        return data
    except json.JSONDecodeError:
        return None


def fetch_openclaw_full_config(openclaw_bin: Optional[str]) -> Optional[dict]:
    """优先读 ~/.openclaw/openclaw.json 的 agents 段，避免每次起 CLI。"""
    sub = _agents_subtree_from_root(load_openclaw_root_config())
    if sub is not None:
        return sub
    if not openclaw_bin:
        return None
    try:
        result = subprocess.run(
            [openclaw_bin, "config", "get", "agents"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            logger.warning("openclaw config get agents failed: %s", result.stderr.strip()[:200])
            return None
        return _parse_first_json_document(result.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception) as e:
        logger.warning("openclaw config get agents parse error: %s", e)
        return None


def build_agent_detail(agent_cfg: dict, defaults: dict) -> dict:
    agent_id = agent_cfg.get("id", "")
    tools_cfg = agent_cfg.get("tools", {})
    profile = tools_cfg.get("profile", "")
    also_allow = tools_cfg.get("alsoAllow", tools_cfg.get("allow", []))
    deny = tools_cfg.get("deny", [])

    skills_cfg = agent_cfg.get("skills", None)
    if skills_cfg == "null" or skills_cfg == "":
        skills_cfg = None
    skills_all = not isinstance(skills_cfg, list)

    return {
        "id": agent_id,
        "name": agent_cfg.get("name", agent_id),
        "workspace": agent_cfg.get("workspace", defaults.get("workspace", "")),
        "agentDir": agent_cfg.get("agentDir", ""),
        "is_default": agent_cfg.get("isDefault", False),
        "model": (
            agent_cfg.get("model", {})
            if isinstance(agent_cfg.get("model"), dict)
            else {"primary": agent_cfg.get("model", "")}
        ),
        "tools": {
            "profile": profile,
            "alsoAllow": also_allow if isinstance(also_allow, list) else [],
            "deny": deny if isinstance(deny, list) else [],
        },
        "skills": skills_cfg if isinstance(skills_cfg, list) else [],
        "skills_all": skills_all,
    }


def get_openclaw_default_workspace(openclaw_bin: Optional[str]) -> Optional[str]:
    sub = _agents_subtree_from_root(load_openclaw_root_config())
    if sub:
        ws = (sub.get("defaults") or {}).get("workspace", "")
        if ws:
            return os.path.expanduser(str(ws))
    if not openclaw_bin:
        return None
    try:
        result = subprocess.run(
            [openclaw_bin, "config", "get", "agents.defaults.workspace"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        ws = result.stdout.strip()
        return os.path.expanduser(ws) if ws else None
    except Exception:
        return None


def get_openclaw_workspace_path(openclaw_bin: Optional[str]) -> Optional[str]:
    if openclaw_bin:
        try:
            result = subprocess.run(
                [openclaw_bin, "config", "get", "agents.defaults.workspace"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line and os.path.sep in line:
                        return line
        except Exception:
            pass

    default_paths = [
        os.path.expanduser("~/.openclaw/workspace"),
        os.path.expanduser("~/.moltbot/workspace"),
        "/projects/.openclaw/workspace",
        "/projects/.moltbot/workspace",
    ]
    for path in default_paths:
        if os.path.isdir(path):
            return path
    return None


def fetch_openclaw_channels(openclaw_bin: Optional[str]) -> Optional[dict]:
    if not openclaw_bin:
        return None
    try:
        result = subprocess.run(
            [openclaw_bin, "channels", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode != 0:
            logger.warning("openclaw channels list failed: %s", result.stderr.strip()[:200])
            return None
        return _parse_first_json_document(result.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception) as e:
        logger.warning("openclaw channels parse error: %s", e)
        return None
