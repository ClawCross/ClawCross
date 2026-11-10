"""Backend-owned controls live beside user workspaces, never inside one."""

import os
from pathlib import Path


def control_path(user_files: Path, user_id: str, filename: str) -> Path:
    if not user_id or user_id in {'.', '..', '.control'} or Path(user_id).name != user_id or '\\' in user_id:
        raise ValueError('Invalid user ID')
    root = (user_files / '.control').resolve()
    path = (root / user_id / filename).resolve()
    if not path.is_relative_to(root) or path.parent == root:
        raise ValueError('Invalid control path')
    return path


def migrate_control_file(legacy: Path, target: Path) -> None:
    """Move existing controls once; subsequent workspace copies are ignored."""
    if target.exists() or not legacy.exists():
        return
    if legacy.is_symlink():
        raise ValueError('Control configuration must not be a symbolic link')
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.rename(legacy, target)
    except FileNotFoundError:
        if not target.exists():
            raise
    target.chmod(0o600)
