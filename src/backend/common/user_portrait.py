"""Text and bounded image attachments for human portraits, without fetching remote URLs."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 256 * 1024
MAX_TEXT = 16000


def portrait_text(profile: dict) -> str:
    if 'text' in profile:
        return str(profile['text'])
    # Keep existing information when the former fields are opened in the unified editor.
    return '\n'.join(f'{label}：{profile[key]}' for key, label in
                     [('avatar', '标记'), ('bio', '简介'), ('location', '地区')] if profile.get(key))


def image_records(values, previous=()) -> list[dict]:
    if not isinstance(values, list) or len(values) > MAX_IMAGES:
        raise ValueError('用户画像最多添加 4 张图片')
    existing = {item['id']: item for item in previous}
    images = []
    for item in values:
        if not isinstance(item, dict):
            raise ValueError('画像图片格式无效')
        if isinstance(item.get('id'), str) and item['id'] in existing and not item.get('url'):
            images.append(existing[item['id']])
            continue
        url = item.get('url', '')
        if not isinstance(url, str) or len(url) > MAX_IMAGE_BYTES * 4 // 3 + 128:
            raise ValueError('每张画像图片压缩后最多 256 KiB')
        header, separator, data = url.partition(',')
        mime = header.removeprefix('data:').removesuffix(';base64')
        if not separator or header != f'data:{mime};base64' or mime not in {'image/jpeg', 'image/png', 'image/webp'}:
            raise ValueError('画像图片仅支持 JPEG、PNG 或 WebP')
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise ValueError('画像图片编码无效') from exc
        signatures = {'image/jpeg': raw.startswith(b'\xff\xd8\xff'), 'image/png': raw.startswith(b'\x89PNG\r\n\x1a\n'),
                      'image/webp': raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'}
        if not raw or len(raw) > MAX_IMAGE_BYTES or not signatures[mime]:
            raise ValueError('画像图片内容无效或超过 256 KiB')
        images.append({'id': hashlib.sha256(raw).hexdigest()[:32], 'name': str(item.get('name') or '画像图片')[:100],
                       'mime': mime, 'url': url})
    return list({image['id']: image for image in images}.values())


def public_portrait(profile: dict) -> dict:
    return {'text': portrait_text(profile), 'images': [
        {k: image[k] for k in ('id', 'name', 'mime')} for image in profile.get('images', [])]}


def read_portrait(root: Path, user: str) -> dict:
    folder = Path(root) / (user or 'anonymous')
    try:
        text = (folder / 'user_profile.txt').read_text(encoding='utf-8').strip()
    except OSError:
        text = ''
    try:
        images = image_records(json.loads((folder / 'user_profile.images.json').read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        images = []
    return {'text': text, 'images': images}


def image_attachments(root: Path, user: str) -> list[dict]:
    return [{'type':'image', 'name':image['name'], 'mime_type':image['mime'], 'data':image['url'].split(',',1)[1]}
            for image in read_portrait(root, user)['images']]
