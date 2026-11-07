"""One bounded call to the primary model, followed by SVG validation. No chat turn."""
import asyncio
from collections import deque
import json
import re
import time

from fastapi import HTTPException
from common.avatars import clean_svg, normalize_avatar

AVATAR_PROMPT = '''Design an original, expressive avatar for a human or AI companion.
Return only one complete, self-contained SVG, under 12000 UTF-8 bytes, with
viewBox="0 0 256 256" and xmlns="http://www.w3.org/2000/svg". It must look clear
at 32 px: a strong silhouette, generous margins, a harmonious palette and an
appealing face or symbol. Use the supplied description as visual preferences.
No words, letters, brand logos, external assets, links, images, use, foreignObject,
scripts, events, href, entities, CSS imports, CSS escapes or external URLs.
Use paths, circles, ellipses, rectangles, groups, gradients and local defs.
For a creature, an optional subtle blink may animate rx/ry/opacity with animate;
keep the eyes open for most of the cycle. No flashing or large motion.
Do not add Markdown, commentary, or configuration instructions.'''


class AvatarGenerator:
    def __init__(self):
        self.active = set()
        self.requests = {}

    async def generate(self, owner, description, name='', kind='agent'):
        now = time.monotonic()
        self.requests = {key: value for key, value in self.requests.items() if value and value[-1] > now - 60}
        history = self.requests.setdefault(owner, deque())
        if owner in self.active or len(self.active) >= 4 or len(history) >= 3:
            raise HTTPException(429, '生成较频繁，请稍后再试')
        history.append(now)
        self.active.add(owner)
        try:
            from common.llm_factory import create_chat_model
            # Use the lowest supported effort. Zero inherits provider defaults,
            # which can consume the whole output budget before producing SVG.
            model = create_chat_model(temperature=0.7, max_tokens=4096, timeout=45, max_retries=0, reasoning_level=1)
            result = await asyncio.wait_for(model.ainvoke([
                ('system', AVATAR_PROMPT),
                ('human', json.dumps({'description': description, 'name': name, 'kind': kind}, ensure_ascii=False)),
            ]), timeout=50)
            content = result.content
            if isinstance(content, list):
                content = ''.join(block.get('text', '') for block in content if isinstance(block, dict))
            match = re.search(r'<svg\b[\s\S]*?</svg>', str(content))
            if not match:
                raise ValueError('No SVG returned')
            svg = clean_svg(match.group())
            return {'svg': svg, 'url': normalize_avatar(svg)}
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, '头像生成未完成，请稍后再试') from None
        finally:
            self.active.discard(owner)
