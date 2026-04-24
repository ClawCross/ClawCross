from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
USER_FILES_DIR = PROJECT_ROOT / "data" / "user_files"


def _list_team_names(user_id: str) -> list[str]:
    scoped_user = (user_id or "").strip()
    if not scoped_user:
        return []
    teams_root = USER_FILES_DIR / scoped_user / "teams"
    if not teams_root.is_dir():
        return []
    return sorted(
        item.name
        for item in teams_root.iterdir()
        if item.is_dir()
    )


def build_team_workflow_prompt(user_id: str, *, team: str = "") -> str:
    """Build a compact team/workflow usage hint for system prompt injection."""
    teams = _list_team_names(user_id)
    if team and team in teams:
        teams = [team] + [item for item in teams if item != team]

    lines = ["\n【Team / Workflow 提示】"]
    if teams:
        lines.append("可用的 team：")
        for name in teams[:20]:
            lines.append(f"  - {name}")
        if len(teams) > 20:
            lines.append(f"  - ... 另有 {len(teams) - 20} 个 team")
    else:
        lines.append("当前未发现可用 team。")

    lines.extend(
        [
            "",
            "查找和执行 workflow 时，遵循下面规则：",
            "1. 内部 agent / 当前会话优先直接用 MCP 工具，不要绕 CLI。",
            "   - 查找 YAML: list_oasis_workflows(team=\"...\")",
            "   - 查找 Python: list_oasis_python_workflows(team=\"...\")",
            "   - 执行 YAML: start_new_oasis(schedule_file=\"...\", team=\"...\", question=\"...\")",
            "   - 执行 Python: start_new_oasis(python_file=\"...\", team=\"...\", question=\"...\")",
            "2. 只有外部 agent 才使用 CLI。",
            "   - 先切换目录: cd <项目根目录>  (例如当前仓库根目录，里面应包含 scripts/cli.py)",
            "   - CLI 基本格式: python3 scripts/cli.py -u <user> workflows <action> [options]",
            "   - 查找全部 workflow: python3 scripts/cli.py -u <user> workflows list",
            "   - 查找某个 team 的 workflow: python3 scripts/cli.py -u <user> workflows list --team <team>",
            "   - 只看 YAML: python3 scripts/cli.py -u <user> workflows list --type yaml [--team <team>]",
            "   - 只看 Python: python3 scripts/cli.py -u <user> workflows list --type python [--team <team>]",
            "   - 运行 YAML workflow: python3 scripts/cli.py -u <user> workflows run --type yaml --team <team> --name <workflow> --question \"<task>\"",
            "   - 运行 Python workflow: python3 scripts/cli.py -u <user> workflows run --type python --team <team> --name <workflow> --question \"<task>\"",
            "   - 如果 workflow 在个人目录而不是 team 目录，可省略 --team。",
            "   - --name 用 workflow 文件名；YAML 可不写 .yaml，Python 可不写 .py。",
            "3. 使用 CLI 时，不要在错误目录下执行；先进入项目根目录再运行命令。",
        ]
    )
    return "\n".join(lines)
