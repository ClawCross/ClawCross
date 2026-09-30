"""The local OpenClaw install: its ``openclaw.json``, CLI and skills.

What ``openclaw_routes`` manages OpenClaw agents with. The config is read from
``openclaw.json`` directly when it exists, else through ``openclaw config get``.
"""

import json
import os
import shutil
import subprocess
from typing import Optional

from common.logging_utils import get_logger

logger = get_logger("external.openclaw_config")

# OpenClaw 2026.x schema: agents.list[].tools.profile 仅允许下列取值
ALLOWED_TOOLS_PROFILES = frozenset({"minimal", "coding", "messaging", "full"})
DEFAULT_TOOLS_PROFILE = "coding"

# `openclaw skills list --json`, loaded once in the background at startup (preload_skills).
skills_info: dict = {}
managed_skills_dir: str = ""
bundled_skills: list = []


def openclaw_bin() -> Optional[str]:
    return (shutil.which("openclaw.cmd") if os.name == "nt" else None) or shutil.which("openclaw")


def sanitize_tools(tools: Optional[dict]) -> dict:
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
    aliases = {
        "code": "coding",
        "default": "coding",
        "dev": "coding",
        "developer": "coding",
    }
    out["profile"] = aliases.get(s.lower(), DEFAULT_TOOLS_PROFILE)
    return out


def sanitize_root_tools_profiles(root: dict) -> int:
    """就地修正 root['agents']['list'][*].tools.profile，返回修正的 agent 条目数。"""
    agents = root.get("agents") if isinstance(root, dict) else None
    lst = agents.get("list") if isinstance(agents, dict) else None
    if not isinstance(lst, list):
        return 0
    fixed = 0
    for entry in lst:
        tools = entry.get("tools") if isinstance(entry, dict) else None
        if not isinstance(tools, dict) or "profile" not in tools:
            continue
        old = tools.get("profile")
        if (str(old).strip() if old is not None else "") in ALLOWED_TOOLS_PROFILES:
            continue
        entry["tools"] = sanitize_tools(tools)
        fixed += 1
    return fixed


def root_config_path() -> str:
    """OPENCLAW_CONFIG_FILE, else $OPENCLAW_HOME/openclaw.json, else ~/.openclaw/openclaw.json."""
    env_file = (os.getenv("OPENCLAW_CONFIG_FILE", "") or "").strip()
    if env_file:
        return os.path.expanduser(env_file)
    env_home = (os.getenv("OPENCLAW_HOME", "") or "").strip()
    if env_home:
        return os.path.join(os.path.expanduser(env_home), "openclaw.json")
    return os.path.expanduser("~/.openclaw/openclaw.json")


def load_root_config() -> Optional[dict]:
    """读取完整 openclaw.json（失败返回 None）。"""
    path = root_config_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("load openclaw.json failed: %s", e)
        return None


def save_root_config(data: dict) -> bool:
    """写回完整 openclaw.json（原子替换）。"""
    path = root_config_path()
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


def _agents_subtree(root: Optional[dict]) -> Optional[dict]:
    if not root or not isinstance(root.get("agents"), dict):
        return None
    return root["agents"]


def parse_first_json(raw: str):
    """The first JSON object or array in CLI output that may start with log lines."""
    if not raw:
        return None
    idx = raw.find("{")
    arr_idx = raw.find("[")
    if idx < 0 or (arr_idx >= 0 and arr_idx < idx):
        idx = arr_idx
    if idx < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(raw[idx:])
        return data
    except json.JSONDecodeError:
        return None


def agents_config() -> Optional[dict]:
    """The ``agents`` section; 优先读 openclaw.json，避免每次起 CLI。"""
    sub = _agents_subtree(load_root_config())
    if sub is not None:
        return sub
    binary = openclaw_bin()
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "config", "get", "agents"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            logger.warning("openclaw config get agents failed: %s", result.stderr.strip()[:200])
            return None
        return parse_first_json(result.stdout)
    except Exception as e:
        logger.warning("openclaw config get agents parse error: %s", e)
        return None


def agent_detail(agent_cfg: dict, defaults: dict) -> dict:
    agent_id = agent_cfg.get("id", "")
    tools_cfg = agent_cfg.get("tools", {})
    also_allow = tools_cfg.get("alsoAllow", tools_cfg.get("allow", []))
    deny = tools_cfg.get("deny", [])

    skills_cfg = agent_cfg.get("skills", None)
    if skills_cfg == "null" or skills_cfg == "":
        skills_cfg = None

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
            "profile": tools_cfg.get("profile", ""),
            "alsoAllow": also_allow if isinstance(also_allow, list) else [],
            "deny": deny if isinstance(deny, list) else [],
        },
        "skills": skills_cfg if isinstance(skills_cfg, list) else [],
        "skills_all": not isinstance(skills_cfg, list),
    }


def default_workspace() -> Optional[str]:
    sub = _agents_subtree(load_root_config())
    if sub:
        ws = (sub.get("defaults") or {}).get("workspace", "")
        if ws:
            return os.path.expanduser(str(ws))
    binary = openclaw_bin()
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "config", "get", "agents.defaults.workspace"],
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


def workspace_path() -> Optional[str]:
    binary = openclaw_bin()
    if binary:
        try:
            result = subprocess.run(
                [binary, "config", "get", "agents.defaults.workspace"],
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

    for path in (
        os.path.expanduser("~/.openclaw/workspace"),
        os.path.expanduser("~/.moltbot/workspace"),
        "/projects/.openclaw/workspace",
        "/projects/.moltbot/workspace",
    ):
        if os.path.isdir(path):
            return path
    return None


def channels() -> Optional[dict]:
    binary = openclaw_bin()
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "channels", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode != 0:
            logger.warning("openclaw channels list failed: %s", result.stderr.strip()[:200])
            return None
        return parse_first_json(result.stdout)
    except Exception as e:
        logger.warning("openclaw channels parse error: %s", e)
        return None


def preload_skills() -> None:
    """Fill the skills cache from ``openclaw skills list --json`` (a 6~13s Node CLI)."""
    global skills_info, managed_skills_dir, bundled_skills

    binary = openclaw_bin()
    if not binary:
        logger.info("openclaw CLI not available, skipping skills preload")
        return
    try:
        result = subprocess.run(
            [binary, "skills", "list", "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning("openclaw skills list failed: %s", result.stderr.strip()[:200])
            return
        idx = result.stdout.find("{")
        if idx < 0:
            logger.warning("Failed to parse openclaw skills list output")
            return
        data = json.loads(result.stdout[idx:])
    except Exception as e:
        logger.warning("Failed to preload openclaw skills: %s", e)
        return

    all_skills = data.get("skills", [])
    managed_skills_dir = data.get("managedSkillsDir", "")
    bundled_skills = [s for s in all_skills if s.get("source") == "openclaw-bundled"]
    skills_info = data
    logger.info("OpenClaw skills preloaded: %d total, %d bundled, managed dir %s",
                len(all_skills), len(bundled_skills), managed_skills_dir)
