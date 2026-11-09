#!/usr/bin/env python3
"""Reorganize ClawCross repo: group src/ files by domain, separate frontend assets.

Target layout:
  frontend/          HTML templates, JS, CSS, static assets
  src/
    core/            Agent core logic
    webot/           WeBot subsystem (14 files)
    mcp/             MCP server integrations (8 files)
    api/             REST routes + services + models (20 files)
    routes/          Flask frontend routes (4 files)
    services/        Shared services (7 files)
    utils/           Utilities (15 files)
    integrations/    External platform adapters (4 files)
    mainagent.py     FastAPI entry
    front.py         Flask entry
"""

import os
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"

SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".mypy_cache"}

# ── File Mapping: old_filename → (subdir, new_filename) ──────────────
# Files NOT listed here stay at src/ top level (mainagent.py, front.py)

FILE_MAP = {
    # ── core/ ──
    "agent.py":                     ("core", "agent.py"),
    "agent_orchestrator.py":        ("core", "agent_orchestrator.py"),
    "agent_runtime_state.py":       ("core", "agent_runtime_state.py"),
    "consensus.py":                 ("core", "consensus.py"),
    "streaming_tool_executor.py":   ("core", "streaming_tool_executor.py"),
    "lazy_tool_discovery.py":       ("core", "lazy_tool_discovery.py"),
    "workflow_engines.py":          ("core", "workflow_engines.py"),

    # ── webot/ ──
    "webot_bridge.py":              ("webot", "bridge.py"),
    "webot_buddy.py":               ("webot", "buddy.py"),
    "webot_context.py":             ("webot", "context.py"),
    "webot_memory.py":              ("webot", "memory.py"),
    "webot_models.py":              ("webot", "models.py"),
    "webot_permission_context.py":  ("webot", "permission_context.py"),
    "webot_policy.py":              ("webot", "policy.py"),
    "webot_profiles.py":            ("webot", "profiles.py"),
    "webot_routes.py":              ("webot", "routes.py"),
    "webot_runtime.py":             ("webot", "runtime.py"),
    "webot_runtime_store.py":       ("webot", "runtime_store.py"),
    "webot_service.py":             ("webot", "service.py"),
    "webot_subagents.py":           ("webot", "subagents.py"),
    "webot_voice.py":               ("webot", "voice.py"),
    "webot_workflow_presets.py":     ("webot", "workflow_presets.py"),
    "webot_workspace.py":           ("webot", "workspace.py"),

    # ── mcp/ ──
    "mcp_commander.py":             ("mcp", "commander.py"),
    "mcp_filemanager.py":           ("mcp", "filemanager.py"),
    "mcp_llmapi.py":                ("mcp", "llmapi.py"),
    "mcp_oasis.py":                 ("mcp", "oasis.py"),
    "mcp_scheduler.py":             ("mcp", "scheduler.py"),
    "mcp_search.py":                ("mcp", "search.py"),
    "mcp_session.py":               ("mcp", "session.py"),
    "mcp_telegram.py":              ("mcp", "telegram.py"),
    "mcp_webot.py":                 ("mcp", "webot.py"),

    # ── api/ ──
    "group_models.py":              ("api", "group_models.py"),
    "group_repository.py":          ("api", "group_repository.py"),
    "group_routes.py":              ("api", "group_routes.py"),
    "group_service.py":             ("api", "group_service.py"),
    "session_models.py":            ("api", "session_models.py"),
    "session_routes.py":            ("api", "session_routes.py"),
    "session_service.py":           ("api", "session_service.py"),
    "openai_models.py":             ("api", "openai_models.py"),
    "openai_protocol.py":           ("api", "openai_protocol.py"),
    "openai_routes.py":             ("api", "openai_routes.py"),
    "openai_service.py":            ("api", "openai_service.py"),
    "ops_models.py":                ("api", "ops_models.py"),
    "ops_routes.py":                ("api", "ops_routes.py"),
    "ops_service.py":               ("api", "ops_service.py"),
    "settings_models.py":           ("api", "settings_models.py"),
    "settings_routes.py":           ("api", "settings_routes.py"),
    "settings_service.py":          ("api", "settings_service.py"),
    "system_models.py":             ("api", "system_models.py"),
    "system_routes.py":             ("api", "system_routes.py"),
    "system_service.py":            ("api", "system_service.py"),

    # ── routes/ (Flask frontend routes) ──
    "front_group_routes.py":        ("routes", "front_group_routes.py"),
    "front_oasis_routes.py":        ("routes", "front_oasis_routes.py"),
    "front_session_routes.py":      ("routes", "front_session_routes.py"),
    "front_webot_routes.py":        ("routes", "front_webot_routes.py"),

    # ── services/ ──
    "llm_factory.py":               ("services", "llm_factory.py"),
    "team_creator_service.py":      ("services", "team_creator_service.py"),
    "team_preset_assets.py":        ("services", "team_preset_assets.py"),
    "tinyfish_monitor_service.py":  ("services", "tinyfish_monitor_service.py"),
    "notification_system.py":       ("services", "notification_system.py"),
    "message_builder.py":           ("services", "message_builder.py"),
    "skill_import_tools.py":        ("services", "skill_import_tools.py"),

    # ── utils/ ──
    "api_patch.py":                 ("utils", "api_patch.py"),
    "auth_utils.py":                ("utils", "auth_utils.py"),
    "bash_safety.py":               ("utils", "bash_safety.py"),
    "cache_boundary.py":            ("utils", "cache_boundary.py"),
    "checkpoint_repository.py":     ("utils", "checkpoint_repository.py"),
    "context_compressor.py":        ("utils", "context_compressor.py"),
    "cost_tracker.py":              ("utils", "cost_tracker.py"),
    "cron_utils.py":                ("utils", "cron_utils.py"),
    "effort_controller.py":         ("utils", "effort_controller.py"),
    "env_settings.py":              ("utils", "env_settings.py"),
    "logging_utils.py":             ("utils", "logging_utils.py"),
    "session_summary.py":           ("utils", "session_summary.py"),
    "time.py":                      ("utils", "time.py"),
    "token_budget.py":              ("utils", "token_budget.py"),
    "user_auth.py":                 ("utils", "user_auth.py"),

    # ── integrations/ ──
    "acpx_adapter.py":              ("integrations", "acpx_adapter.py"),
    "acpx_cli_tools.py":            ("integrations", "acpx_cli_tools.py"),
    "openclaw_restore_naming.py":   ("integrations", "openclaw_restore_naming.py"),
    "restore_timing_log.py":        ("integrations", "restore_timing_log.py"),
}

# ── Build import rewrite map ─────────────────────────────────────────
# old_module_name → new_dotted_path (without .py)

def build_import_map():
    """Build mapping: old_module → new_import_path."""
    m = {}
    for old_file, (subdir, new_file) in FILE_MAP.items():
        old_mod = old_file.removesuffix(".py")
        new_mod = f"{subdir}.{new_file.removesuffix('.py')}"
        m[old_mod] = new_mod
    return m

IMPORT_MAP = build_import_map()

# Sort by length descending to avoid partial matches
SORTED_OLD_MODS = sorted(IMPORT_MAP.keys(), key=len, reverse=True)


# ── Step 1: Create directories ───────────────────────────────────────

def create_dirs():
    """Create subdirectories and __init__.py files."""
    subdirs = {"core", "webot", "mcp", "api", "routes", "services", "utils", "integrations"}
    for d in subdirs:
        p = SRC / d
        p.mkdir(exist_ok=True)
        init = p / "__init__.py"
        if not init.exists():
            init.write_text("")
    # frontend directory
    fe = PROJECT_ROOT / "frontend"
    for d in ["templates", "js", "css", "vendor", "assets", "assets/audio"]:
        (fe / d).mkdir(parents=True, exist_ok=True)


# ── Step 2: Move frontend assets ─────────────────────────────────────

def move_frontend():
    """Move src/static/ and src/templates/ to frontend/."""
    fe = PROJECT_ROOT / "frontend"

    moves = [
        (SRC / "templates", fe / "templates"),
        (SRC / "static" / "js", fe / "js"),
        (SRC / "static" / "css", fe / "css"),
        (SRC / "static" / "vendor", fe / "vendor"),
        (SRC / "static" / "assets", fe / "assets"),
    ]

    for src_dir, dst_dir in moves:
        if not src_dir.exists():
            continue
        for item in src_dir.iterdir():
            dst = dst_dir / item.name
            if item.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
        shutil.rmtree(src_dir)

    # Clean up empty src/static/ if nothing left
    static_dir = SRC / "static"
    if static_dir.exists():
        remaining = list(static_dir.rglob("*"))
        if not any(f.is_file() for f in remaining):
            shutil.rmtree(static_dir)


# ── Step 3: Move Python files ────────────────────────────────────────

def move_python_files():
    """Move src/*.py into subdirectories per FILE_MAP."""
    moved = 0
    for old_file, (subdir, new_file) in FILE_MAP.items():
        old_path = SRC / old_file
        new_path = SRC / subdir / new_file
        if old_path.exists():
            shutil.move(str(old_path), str(new_path))
            moved += 1
    print(f"  Moved {moved} Python files")


# ── Step 4: Rewrite imports ──────────────────────────────────────────

# Pattern: from <module> import ... OR import <module>
# We need to match module names at word boundaries in import statements

def rewrite_imports_in_file(path: Path, dry_run=False) -> int:
    """Rewrite import statements in a single file."""
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0

    original = content
    count = 0

    for old_mod in SORTED_OLD_MODS:
        new_mod = IMPORT_MAP[old_mod]

        # Pattern 1: from old_mod import ...
        pattern1 = rf'\bfrom\s+{re.escape(old_mod)}\s+import\b'
        replacement1 = f'from {new_mod} import'
        content, n = re.subn(pattern1, replacement1, content)
        count += n

        # Pattern 2: import old_mod (standalone or in comma list)
        pattern2 = rf'^(\s*)import\s+{re.escape(old_mod)}\b'
        replacement2 = rf'\1import {new_mod}'
        content, n = re.subn(pattern2, replacement2, content, flags=re.MULTILINE)
        count += n

        # Pattern 3: from src.old_mod import ... (used by oasis/ files)
        pattern3 = rf'\bfrom\s+src\.{re.escape(old_mod)}\s+import\b'
        replacement3 = f'from src.{new_mod} import'
        content, n = re.subn(pattern3, replacement3, content)
        count += n

    if content != original and not dry_run:
        path.write_text(content, encoding="utf-8")

    return count


def rewrite_all_imports(dry_run=False):
    """Rewrite imports in all Python files across the project."""
    total = 0
    dirs_to_scan = [SRC, PROJECT_ROOT / "oasis", PROJECT_ROOT / "test",
                    PROJECT_ROOT / "scripts", PROJECT_ROOT / "chatbot",
                    PROJECT_ROOT / "packaging", PROJECT_ROOT / "visual"]

    for scan_dir in dirs_to_scan:
        if not scan_dir.exists():
            continue
        for root, dirs, files in os.walk(scan_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                if f.endswith(".py"):
                    p = Path(root) / f
                    n = rewrite_imports_in_file(p, dry_run)
                    if n > 0:
                        rel = p.relative_to(PROJECT_ROOT)
                        print(f"  {'🔍' if dry_run else '✅'} {rel} ({n} imports)")
                        total += n

    print(f"  Total: {total} import rewrites")
    return total


# ── Step 5: Update Flask paths in front.py ───────────────────────────

def update_flask_paths():
    """Update template_folder and static_folder in front.py."""
    fp = SRC / "front.py"
    content = fp.read_text(encoding="utf-8")
    original = content

    # Update Flask app init to point to frontend/
    content = content.replace(
        "template_folder=os.path.join(current_dir, 'templates')",
        "template_folder=os.path.join(root_dir, 'frontend', 'templates')"
    )
    content = content.replace(
        "static_folder=os.path.join(current_dir, 'static')",
        "static_folder=os.path.join(root_dir, 'frontend')"
    )

    if content != original:
        fp.write_text(content, encoding="utf-8")
        print("  ✅ Updated Flask paths in front.py")


# ── Step 6: Update sys.path inserts ──────────────────────────────────

def update_sys_path_refs():
    """Update any sys.path references that point to src/ subdirectories."""
    # oasis/engine.py and oasis/experts.py insert src/ into path - that's still correct
    # since we keep src/ as the package root. No changes needed for sys.path.
    print("  ℹ️  sys.path references still valid (src/ remains on path)")


# ── Step 7: Update HTML templates ────────────────────────────────────

def update_html_templates():
    """Update static asset paths in HTML templates."""
    template_dir = PROJECT_ROOT / "frontend" / "templates"
    if not template_dir.exists():
        return

    count = 0
    for html_file in template_dir.glob("*.html"):
        content = html_file.read_text(encoding="utf-8")
        original = content

        # /static/js/xxx → /static/js/xxx (Flask serves frontend/ as static)
        # /static/css/xxx → /static/css/xxx
        # /static/vendor/xxx → /static/vendor/xxx
        # /static/assets/xxx → /static/assets/xxx
        # These paths are served by Flask's static route, so they depend on
        # how static_folder is configured. Since we set static_folder to
        # frontend/, the URL /static/js/main.js maps to frontend/js/main.js
        # which is correct.

        if content != original:
            html_file.write_text(content, encoding="utf-8")
            count += 1

    print(f"  ✅ HTML templates: {count} files updated")


# ── Step 8: Update shell scripts & launcher ──────────────────────────

def update_shell_scripts():
    """Update process paths in shell scripts."""
    manual_run = PROJECT_ROOT / "manual_run.sh"
    if manual_run.exists():
        content = manual_run.read_text(encoding="utf-8")
        original = content
        # time.py moved to utils/time.py
        content = content.replace("src/time.py", "src/utils/time.py")
        if content != original:
            manual_run.write_text(content, encoding="utf-8")
            print("  ✅ Updated manual_run.sh")

    # scripts/launcher.py - update process launch commands
    launcher = PROJECT_ROOT / "scripts" / "launcher.py"
    if launcher.exists():
        content = launcher.read_text(encoding="utf-8")
        original = content
        content = content.replace('"src/time.py"', '"src/utils/time.py"')
        content = content.replace("'src/time.py'", "'src/utils/time.py'")
        if content != original:
            launcher.write_text(content, encoding="utf-8")
            print("  ✅ Updated scripts/launcher.py")


# ── Step 9: Update JS build tools ────────────────────────────────────

def update_build_tools():
    """Update esbuild output paths in build tools."""
    for tool_file in (PROJECT_ROOT / "tools").glob("*.mjs"):
        content = tool_file.read_text(encoding="utf-8")
        original = content
        content = content.replace("src/static/js/", "frontend/js/")
        content = content.replace("src/static/vendor/", "frontend/vendor/")
        if content != original:
            tool_file.write_text(content, encoding="utf-8")
            print(f"  ✅ Updated {tool_file.name}")


# ── Main ─────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("🔍 DRY RUN — no changes will be made\n")

    print("📁 Step 1: Creating directories...")
    if not dry_run:
        create_dirs()
    print("  ✅ Done\n")

    print("📦 Step 2: Moving frontend assets...")
    if not dry_run:
        move_frontend()
    print("  ✅ Done\n")

    print("🐍 Step 3: Moving Python files...")
    if not dry_run:
        move_python_files()
    print()

    print("📝 Step 4: Rewriting imports...")
    rewrite_all_imports(dry_run)
    print()

    print("🌐 Step 5: Updating Flask paths...")
    if not dry_run:
        update_flask_paths()
    print()

    print("🔧 Step 6: Checking sys.path refs...")
    update_sys_path_refs()
    print()

    print("📄 Step 7: Updating HTML templates...")
    if not dry_run:
        update_html_templates()
    print()

    print("🖥️  Step 8: Updating shell scripts...")
    if not dry_run:
        update_shell_scripts()
    print()

    print("🏗️  Step 9: Updating build tools...")
    if not dry_run:
        update_build_tools()
    print()

    print(f"\n{'🔍 DRY RUN complete' if dry_run else '✅ Reorganization complete!'}")


if __name__ == "__main__":
    main()
