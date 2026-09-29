"""
Consensus Vote Parsing – ported from openclaw-claude-code/src/consensus.ts.

Detects [CONSENSUS: YES/NO] tags in agent responses with multiple
fallback patterns for variant formats, including Chinese consensus votes.

Priority chain:
1. Strict format: [CONSENSUS: YES] / [CONSENSUS: NO] (supports Chinese colon)
2. Common variants: consensus: yes, **consensus**: no, CONSENSUS=YES, 共识投票
3. Tail fallback: analyse last 8 non-empty lines for positive/negative signals
4. Default: False (no consensus)
"""

from __future__ import annotations

import re


def strip_consensus_tags(text: str) -> str:
    """Remove all [CONSENSUS: YES/NO] tags from text."""
    return re.sub(r'\[\s*CONSENSUS\s*[:：]\s*(?:YES|NO)\s*\]', '', text, flags=re.IGNORECASE).strip()


def has_consensus_marker(text: str) -> bool:
    """Check whether text contains any consensus vote marker."""
    return bool(
        re.search(r'\[\s*CONSENSUS\s*[:：]\s*(?:YES|NO)\s*\]', text, re.IGNORECASE)
        or re.search(r'consensus[:\s]+(yes|no)', text, re.IGNORECASE)
        or re.search(r'共识投票[:：\s]+(YES|NO)', text, re.IGNORECASE)
    )


def parse_consensus(content: str) -> bool:
    """
    Parse a consensus vote from agent response text.

    Mirrors openclaw-claude-code's parseConsensus() exactly:
    - Strict format with Chinese colon support — take the LAST match
    - Variant patterns — also take the last match
    - Tail fallback — analyse last 8 non-empty lines
    - Default: False
    """
    # 1. Strict format (supports Chinese colon ：) — take the LAST match
    strict_matches = list(re.finditer(
        r'\[\s*CONSENSUS\s*[:：]\s*(YES|NO)\s*\]', content, re.IGNORECASE
    ))
    if strict_matches:
        return strict_matches[-1].group(1).upper() == 'YES'

    # 2. Fallback: common variants — also take the last match
    variant_patterns = [
        r'consensus[:\s]+(yes|no)',
        r'\*\*consensus\*\*[:\s]+(yes|no)',
        r'CONSENSUS=(YES|NO)',
        r'共识投票[:：\s]+(YES|NO)',
        r'\[CONSENSUS\][:\s]+(YES|NO)',
    ]
    for pattern in variant_patterns:
        matches = list(re.finditer(pattern, content, re.IGNORECASE))
        if matches:
            return matches[-1].group(1).upper() == 'YES'

    # 3. Last resort: analyse final 8 non-empty lines
    last_lines = ' '.join(
        line for line in content.split('\n')
        if line.strip()
    )
    # Only look at last ~500 chars worth of non-empty lines
    tail = last_lines[-2000:].lower() if len(last_lines) > 2000 else last_lines.lower()

    has_negative = (
        'consensus: no' in tail
        or 'consensus no' in tail
        or bool(re.search(r'\b(?:not|no)\s+(?:reach(?:ed)?|achieve(?:d)?)\s+consensus\b', tail))
        or bool(re.search(r'(?:未|没|沒有|没有)达成共识', tail))
    )
    if has_negative:
        return False

    has_positive = (
        'consensus: yes' in tail
        or 'consensus yes' in tail
        or bool(re.search(r'达成共识', tail))
    )
    if has_positive:
        return True

    return False
