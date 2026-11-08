"""Explicit local ACP launches. Resolving a profile never downloads packages."""
import os
import json
import shlex
import shutil
from pathlib import Path

try:
    from common.runtime_paths import BIN_DIR, CONFIG_DIR
except ModuleNotFoundError:  # Installer runs before backend import paths exist.
    from src.backend.common.runtime_paths import BIN_DIR, CONFIG_DIR

PACKAGES = json.loads((Path(__file__).resolve().parents[3] / 'config/acp-adapters.json').read_text())
LAUNCHERS = json.loads((Path(__file__).resolve().parents[3] / 'config/acp-launchers.json').read_text())


def direct_default(config):
    return True


def set_default(config):
    config.setdefault('meta', {}).setdefault('acp', {})['transport'] = 'direct'


def alias(name):
    # Read old aliases as data for migration; never import or execute acpx.
    for path in (CONFIG_DIR/'acp-launchers.json', Path.home()/'.acpx/config.json'):
        try:
            if path.stat().st_size > 1024*1024: continue
            value = json.loads(path.read_text()).get('agents', {}).get(name)
            if isinstance(value, dict): value = value.get('command')
            if value: return value
        except (OSError, ValueError): pass
    raise RuntimeError(f'ACP launcher alias {name!r} is not configured')


def argv(value):
    parts = value if isinstance(value, list) else shlex.split(value, posix=os.name != 'nt')
    if not parts or len(parts)>128 or any(not isinstance(p,str) or '\0' in p for p in parts):
        raise RuntimeError('Invalid ACP launcher argv')
    return list(parts)


def launch_spec(config):
    """Portable launch description, also resolved by the remote stdio worker."""
    from external.acp_launcher import launch_settings
    target = launch_settings(config)
    platform = config.get('platform')
    if target['agent_name']: return {'alias':target['agent_name']}
    if target['agent_command']: return {'argv':argv(target['agent_command'])}
    if platform in PACKAGES:
        package, version, binary = PACKAGES[platform]
        return {'package':package,'version':version,'binary':binary,'node':'node'}
    if platform in LAUNCHERS: return {'argv':list(LAUNCHERS[platform])}
    raise RuntimeError('Configure an installed ACP program for this platform')


def command(config):
    spec = launch_spec(config)
    if spec.get('alias'): parts = argv(alias(spec['alias']))
    elif spec.get('argv'): parts = spec['argv']
    else:
        script = BIN_DIR/'node/node_modules'/spec['package']/'dist/index.js'
        node = shutil.which('node')
        if node and script.is_file(): return [node,str(script)]
        installed = shutil.which(spec['binary'])
        if installed: return [installed]
        raise RuntimeError(f'ACP adapter not installed; run clawcross install-component acp-{config["platform"]}')
    # argv only: never expand shell operators or command substitutions.
    if os.name == 'nt': parts = [p.strip('"') for p in parts]
    local = BIN_DIR/'node/node_modules/.bin'/(parts[0]+('.cmd' if os.name=='nt' else ''))
    parts[0] = shutil.which(parts[0]) or str(local if local.is_file() else parts[0])
    return parts
