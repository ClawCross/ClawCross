"""Local account selection shared by setup, CLI and the localhost login UI.

This is identity metadata, not a new authentication mechanism. Remote requests
still need the selected account's password or existing login capability.
"""
import json
import os
import re

from .runtime_paths import USER_FILES_DIR, USERS_FILE


def accounts():
    result = []
    if USERS_FILE.exists():
        users = json.loads(USERS_FILE.read_text(encoding='utf-8'))
        if not isinstance(users, dict):
            raise ValueError('Invalid users.json')
        result.extend(users)
    if USER_FILES_DIR.is_dir():
        # Directory creation is the existing passwordless local setup flow.
        result.extend(p.name for p in sorted(USER_FILES_DIR.iterdir(), key=lambda p: p.stat().st_mtime)
                      if p.is_dir() and not p.is_symlink())
    return list(dict.fromkeys(name for name in result if re.fullmatch(r'[A-Za-z0-9_-]{1,50}', name)))


def default_account(*, create=False):
    names = accounts()
    name = next((os.environ[key].strip() for key in ('CLAW_USER', 'CLI_USER')
                 if os.environ.get(key, '').strip()), None) or (names[0] if names else 'default')
    return select_account(name, create=create)


def select_account(name, *, create=False):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', name):
        raise ValueError('Invalid local account')
    if create:
        path = USER_FILES_DIR / name
        if path.is_symlink():
            raise ValueError('Account directory cannot be a symbolic link')
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return name
