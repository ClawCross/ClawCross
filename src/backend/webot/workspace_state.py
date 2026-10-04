"""Small live workspace observations for the existing runtime-context updates."""
from collections import OrderedDict, deque
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import threading


MAX_ENTRIES = 512
MAX_DIRECTORIES = 64
MAX_DEPTH = 6
DISPLAY_LIMIT = 24
EXCLUDED_DIRECTORIES = frozenset({'.git', '.hg', '.svn', 'node_modules', '.venv',
                                 'venv', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'})
_observations = OrderedDict()
_lock = threading.Lock()


def _scan(root):
    records, errors = {}, []
    queue = deque([(Path(root), 0)])
    directories = 0
    truncated = False
    while queue and len(records) < MAX_ENTRIES and directories < MAX_DIRECTORIES:
        directory, depth = queue.popleft()
        directories += 1
        try:
            # Bound enumeration as well as output; dependency trees are skipped.
            with os.scandir(directory) as stream:
                entries = list(islice(stream, MAX_ENTRIES - len(records) + 1))
            if len(entries) > MAX_ENTRIES - len(records):
                truncated = True
            entries.sort(key=lambda entry: entry.name)
        except OSError:
            errors.append(str(directory.relative_to(root)) or '.')
            continue
        for entry in entries:
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
                if is_directory and entry.name in EXCLUDED_DIRECTORIES:
                    continue
                if len(records) >= MAX_ENTRIES:
                    truncated = True
                    break
                relative = str(Path(entry.path).relative_to(root))
                if is_directory:
                    records[relative] = ('directory',)
                    if depth < MAX_DEPTH:
                        queue.append((Path(entry.path), depth + 1))
                    else:
                        truncated = True
                else:
                    stat = entry.stat(follow_symlinks=False)
                    records[relative] = ('link' if entry.is_symlink() else 'file',
                                         stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
            except OSError:
                errors.append(str(Path(entry.path).relative_to(root)))
    return records, bool(truncated or queue), sorted(errors)


def observe_workspace(root: Path, *, user_id: str, session_id: str) -> dict:
    """Read names/stat only. Stable observations emit no repeated context changes.

    Keep the latest detected change until another change, so a failed model
    request does not consume it. This adds no watcher, timer or Agent wake-up.
    """
    root = root.resolve()
    key = (user_id, session_id, str(root))
    with _lock:
        records, truncated, errors = _scan(root)
        revision = hashlib.sha256(json.dumps([sorted(records.items()), truncated, errors],
                                            ensure_ascii=False).encode()).hexdigest()[:16]
        previous = _observations.pop(key, None)
        if previous and previous['state']['revision'] == revision:
            _observations[key] = previous
            return previous['state']
        changes = {}
        if previous:
            old = previous['records']
            changes['added'] = sorted(set(records) - set(old))[:DISPLAY_LIMIT]
            changes['modified'] = sorted(path for path in records.keys() & old.keys()
                                         if records[path] != old[path])[:DISPLAY_LIMIT]
            # Partial visibility is not evidence that a file was deleted.
            changes['removed'] = (sorted(set(old) - set(records))[:DISPLAY_LIMIT]
                                  if not (truncated or errors or previous['limited']) else [])
        state = {'revision': revision, 'observed_entries': len(records),
                 'files': [path for path, stat in sorted(records.items()) if stat[0] != 'directory'][:DISPLAY_LIMIT],
                 'directories': [path for path, stat in sorted(records.items()) if stat[0] == 'directory' and '/' not in path][:DISPLAY_LIMIT],
                 'recent_changes': changes,
                 'limited': bool(truncated or errors),
                 'scope': '文件名与元数据的有界观察，不读取正文；跳过依赖及缓存目录，非完整文件清单。'}
        if errors:
            state['unreadable'] = errors[:DISPLAY_LIMIT]
        _observations[key] = {'records': records, 'state': state, 'limited': bool(truncated or errors)}
        while len(_observations) > 256:
            _observations.popitem(last=False)
        return state
