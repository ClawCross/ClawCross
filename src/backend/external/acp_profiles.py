"""Explicit local ACP launches. Resolving a profile never downloads packages."""
import os
import json
import shlex
import shutil
from pathlib import Path

try:
    from common.runtime_paths import BIN_DIR
except ModuleNotFoundError:  # Installer runs before backend import paths exist.
    from src.backend.common.runtime_paths import BIN_DIR

PACKAGES = json.loads((Path(__file__).resolve().parents[3] / 'config/acp-adapters.json').read_text())


def direct_default(config):
    from external.acp_launcher import launch_settings
    target = launch_settings(config)
    if os.name == 'nt' and config.get('platform') not in PACKAGES:
        return False
    return not config.get('connection_id') and not target['agent_name'] and bool(
        target['agent_command'] or config.get('platform') in PACKAGES)


def set_default(config):
    if direct_default(config):
        config.setdefault('meta', {}).setdefault('acp', {}).setdefault('transport', 'direct')


def command(config):
    from external.acp_launcher import launch_settings
    target = launch_settings(config)
    if target['agent_name']:
        raise RuntimeError('Named acpx profiles require transport=acpx')
    if target['agent_command']:
        # argv only: no shell, command substitution, or expansion.
        argv = shlex.split(target['agent_command'], posix=os.name != 'nt')
        if os.name == 'nt':
            argv = [part.strip('"') for part in argv]
        if not argv:
            raise RuntimeError('Empty ACP command')
        executable = shutil.which(argv[0])
        local = BIN_DIR / 'node/node_modules/.bin' / (argv[0] + ('.cmd' if os.name == 'nt' else ''))
        argv[0] = executable or str(local if local.is_file() else argv[0])
        return argv
    package = PACKAGES.get(config.get('platform'))
    if not package:
        raise RuntimeError('This platform needs an explicit ACP command or transport=acpx')
    name, _, _ = package
    script = BIN_DIR / 'node/node_modules' / name / 'dist/index.js'
    node = shutil.which('node')
    if not node or not script.is_file():
        raise RuntimeError(f'ACP adapter not installed; run clawcross install-component acp-{config["platform"]}')
    return [node, str(script)]
