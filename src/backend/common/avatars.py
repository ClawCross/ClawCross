"""Small, self-contained avatars. Visual identity never enters a model's portrait."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import struct
from urllib.parse import unquote_to_bytes
from xml.etree import ElementTree as ET

from common.runtime_paths import USER_FILES_DIR

MAX_SVG_BYTES = 12000
MAX_IMAGE_BYTES = 64 * 1024
MAX_AVATAR_INPUT = 100000
AVATAR_ID = re.compile(r'av_[a-f0-9]{64}\Z')
SVG_NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG_NS)
SVG_ELEMENTS = {'svg', 'g', 'path', 'circle', 'ellipse', 'rect', 'line', 'polyline', 'polygon',
                'defs', 'lineargradient', 'radialgradient', 'stop', 'clippath', 'mask', 'pattern',
                'style', 'title', 'desc', 'animate', 'animatetransform', 'set', 'filter',
                'fegaussianblur', 'feoffset', 'femerge', 'femergenode', 'feflood', 'fecomposite',
                'feblend', 'fecolormatrix', 'fedropshadow'}
ANIMATION_ATTRIBUTES = {'transform', 'opacity', 'fill', 'fill-opacity', 'stroke', 'stroke-width',
                        'stroke-opacity', 'stroke-dashoffset', 'd', 'r', 'rx', 'ry', 'cx', 'cy',
                        'x', 'y', 'x1', 'x2', 'y1', 'y2', 'width', 'height'}


def clean_svg(value: str) -> str:
    value = value.strip()
    if len(value.encode()) > MAX_SVG_BYTES:
        raise ValueError('SVG 头像最多 12 KB')
    value = re.sub(r'<!--[\s\S]*?-->', '', value).strip()
    if not value.startswith('<svg') or not value.endswith('</svg>') or len(value.encode()) > MAX_SVG_BYTES:
        raise ValueError('SVG 头像最多 12 KB，且必须是完整的 SVG')
    if re.search(r'<!|<\?|javascript:|data:|\\', value, re.I):
        raise ValueError('SVG 头像包含不支持的内容')
    try:
        root = ET.fromstring(value)
    except ET.ParseError as exc:
        raise ValueError('SVG 格式无效') from exc
    nodes = list(root.iter())
    if len(nodes) > 600 or root.tag not in {'svg', '{' + SVG_NS + '}svg'}:
        raise ValueError('SVG 头像过于复杂')
    for node in nodes:
        tag = node.tag.rsplit('}', 1)[-1].lower()
        if tag not in SVG_ELEMENTS or ('}' in node.tag and not node.tag.startswith('{' + SVG_NS + '}')):
            raise ValueError('SVG 头像仅支持图形与安全动画')
        if tag in {'animate', 'animatetransform', 'set'} and node.get('attributeName', '') not in ANIMATION_ATTRIBUTES:
            raise ValueError('SVG 动画属性无效')
        for key, content in node.attrib.items():
            attribute = key.rsplit('}', 1)[-1].lower()
            if attribute.startswith('on') or attribute in {'href', 'src'} or ('}' in key and not key.startswith('{http://www.w3.org/XML/1998/namespace}')):
                raise ValueError('SVG 头像不能执行脚本或引用外部内容')
            _local_css(content)
        if tag == 'style':
            _local_css(node.text or '')
        if '}' not in node.tag:
            node.tag = '{' + SVG_NS + '}' + node.tag
    if 'viewBox' not in root.attrib:
        root.set('viewBox', '0 0 256 256')
    result = ET.tostring(root, encoding='unicode')
    if len(result.encode()) > MAX_SVG_BYTES:
        raise ValueError('SVG 头像最多 12 KB')
    return result


def _local_css(value):
    if re.search(r'javascript:|data:|\\|https?://|//', value, re.I):
        raise ValueError('SVG 头像不能引用外部内容')
    if any(rule.lower() not in {'keyframes', '-webkit-keyframes', 'media'} for rule in re.findall(r'@([\w-]+)', value)):
        raise ValueError('SVG 头像不支持外部样式或字体')
    for target in re.findall(r'url\s*\((.*?)\)', value, re.I | re.S):
        if not re.fullmatch(r'''["']?#[A-Za-z_][\w.-]*["']?''', target.strip()):
            raise ValueError('SVG 头像不能引用外部内容')


def _dimensions(raw: bytes, mime: str):
    if mime == 'image/png' and raw.startswith(b'\x89PNG\r\n\x1a\n') and raw[12:16] == b'IHDR' and len(raw) >= 24:
        return struct.unpack('>II', raw[16:24])
    if mime == 'image/jpeg' and raw.startswith(b'\xff\xd8\xff'):
        i = 2
        while i + 9 < len(raw):
            if raw[i] != 255:
                break
            while i < len(raw) and raw[i] == 255:
                i += 1
            marker = raw[i]
            i += 1
            if marker in {0xd8, 0xd9} or 0xd0 <= marker <= 0xd7:
                continue
            length = int.from_bytes(raw[i:i + 2], 'big')
            if length < 2:
                break
            if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}:
                return int.from_bytes(raw[i + 5:i + 7], 'big'), int.from_bytes(raw[i + 3:i + 5], 'big')
            i += length
    if mime == 'image/webp' and raw.startswith(b'RIFF') and raw[8:12] == b'WEBP':
        if raw[12:16] == b'VP8X' and len(raw) >= 30:
            return 1 + int.from_bytes(raw[24:27], 'little'), 1 + int.from_bytes(raw[27:30], 'little')
        if raw[12:16] == b'VP8L' and len(raw) >= 25 and raw[20] == 0x2f:
            bits = int.from_bytes(raw[21:25], 'little')
            return 1 + (bits & 0x3fff), 1 + ((bits >> 14) & 0x3fff)
        marker = raw.find(b'\x9d\x01\x2a', 20, 40)
        if marker >= 0 and len(raw) >= marker + 7:
            return int.from_bytes(raw[marker + 3:marker + 5], 'little') & 0x3fff, int.from_bytes(raw[marker + 5:marker + 7], 'little') & 0x3fff
    raise ValueError('头像图片格式无效')


def normalize_avatar(value: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_AVATAR_INPUT:
        raise ValueError('头像过大')
    value = value.strip()
    if not value:
        return ''
    if value.startswith('<svg'):
        svg = clean_svg(value)
        return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()
    header, separator, data = value.partition(',')
    if not separator or not header.startswith('data:image/'):
        raise ValueError('请选择图片或生成 SVG 头像')
    mime = header[5:].split(';', 1)[0]
    try:
        raw = base64.b64decode(data, validate=True) if header.endswith(';base64') else unquote_to_bytes(data)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError('头像编码无效') from exc
    if mime == 'image/svg+xml':
        try:
            return normalize_avatar(clean_svg(raw.decode('utf-8')))
        except UnicodeError as exc:
            raise ValueError('SVG 编码无效') from exc
    if mime not in {'image/png', 'image/jpeg', 'image/webp'} or not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('头像仅支持 PNG、JPEG、WebP 和 SVG，图片压缩后最多 64 KB')
    width, height = _dimensions(raw, mime)
    if not (0 < width <= 512 and 0 < height <= 512):
        raise ValueError('头像图片尺寸过大')
    return f'data:{mime};base64,' + base64.b64encode(raw).decode()


def avatar_id(value: str) -> str:
    return 'av_' + hashlib.sha256(value.encode()).hexdigest() if value else ''


class AvatarStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else Path(USER_FILES_DIR) / '.avatars'

    def folder(self, owner):
        return self.root / hashlib.sha256(str(owner).encode()).hexdigest()

    def _write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name('.' + secrets.token_hex(12) + '.tmp')
        try:
            temporary.write_text(json.dumps(value), encoding='utf-8')
            if os.name != 'nt':
                temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def put(self, owner, value):
        if isinstance(value, str) and AVATAR_ID.fullmatch(value):
            if not self.get(owner, value):
                raise ValueError('头像不存在')
            return value
        url = normalize_avatar(value)
        key = avatar_id(url)
        if key:
            target = self.folder(owner) / (key + '.json')
            if not target.exists() and len(list(self.folder(owner).glob('av_*.json'))) >= 512:
                raise ValueError('保存的头像过多，请清理旧头像后再试')
            self._write(target, {'url': url})
        return key

    def get(self, owner, key):
        if not isinstance(key, str) or not AVATAR_ID.fullmatch(key):
            return ''
        try:
            return json.loads((self.folder(owner) / (key + '.json')).read_text())['url']
        except (OSError, ValueError, KeyError, TypeError):
            return ''

    def user(self, owner):
        configured = True
        try:
            key = json.loads((self.folder(owner) / 'user.json').read_text())['avatar']
        except (OSError, ValueError, KeyError, TypeError):
            key = ''
            configured = False
        return {'avatar': key, 'url': self.get(owner, key), 'configured': configured}

    def save_user(self, owner, value):
        key = self.put(owner, value)
        self._write(self.folder(owner) / 'user.json', {'avatar': key})
        return self.user(owner)
