"""Local group service address and a machine-only control credential."""
import os
from pathlib import Path
import secrets
import time

from common.runtime_paths import DATA_DIR


def service_url():
    return f"http://127.0.0.1:{int(os.getenv('PORT_GROUPS', '51203'))}"


def service_key():
    path = Path(DATA_DIR) / 'group-service.key'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        for _ in range(50):
            key = path.read_text().strip()
            if len(key) >= 32:
                return key
            time.sleep(0.02)
        raise RuntimeError('group-service.key 不完整，请检查文件后重新启动')
    key = secrets.token_urlsafe(32)
    with os.fdopen(fd, 'w') as stream:
        stream.write(key)
    return key
