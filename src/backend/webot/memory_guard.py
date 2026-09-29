"""
Memory Injection Detection — security scanning for memory entries.

Ported from Hermes Agent's memory security:
- Detects prompt injection attempts
- Detects role hijacking
- Detects exfiltration payloads
- Detects invisible unicode
- Blocks deception patterns
"""

from __future__ import annotations

import re
from typing import NamedTuple


class ScanResult(NamedTuple):
    safe: bool
    violations: list[str]


# Threat patterns — ordered from most to least specific
_THREAT_PATTERNS: list[tuple[str, str]] = [
    # Prompt injection
    (r"(?i)ignore\s+(all\s+)?previous\s+instructions", "prompt injection: ignore previous instructions"),
    (r"(?i)disregard\s+(all\s+)?(prior|previous|above)\s+", "prompt injection: disregard prior instructions"),
    (r"(?i)forget\s+(everything|all)\s+(you|about)", "prompt injection: forget context"),
    (r"(?i)new\s+instructions?\s*:", "prompt injection: new instructions block"),

    # Role hijacking
    (r"(?i)you\s+are\s+now\s+", "role hijacking: identity reassignment"),
    (r"(?i)act\s+as\s+(if\s+you\s+are|a)\s+", "role hijacking: act-as directive"),
    (r"(?i)pretend\s+(to\s+be|you\s+are)\s+", "role hijacking: pretend directive"),
    (r"(?i)from\s+now\s+on\s*,?\s*you\s+(are|will)", "role hijacking: identity shift"),

    # Deception hiding
    (r"(?i)do\s+not\s+tell\s+(the\s+)?user", "deception: hide from user"),
    (r"(?i)don'?t\s+(let|tell|inform)\s+(the\s+)?user", "deception: conceal from user"),
    (r"(?i)keep\s+(this\s+)?(secret|hidden)\s+from", "deception: secrecy directive"),
    (r"(?i)never\s+reveal\s+(this|that|the)", "deception: non-disclosure directive"),

    # System prompt override
    (r"(?i)system\s*prompt\s*override", "system prompt override attempt"),
    (r"(?i)\[SYSTEM\]\s*:", "fake system message injection"),
    (r"(?i)<\|system\|>", "system delimiter injection"),

    # Exfiltration
    (r"curl\s+.*\$\{?\w*(TOKEN|KEY|SECRET|PASS|CRED)", "exfiltration: curl with secrets"),
    (r"wget\s+.*\$\{?\w*(TOKEN|KEY|SECRET|PASS|CRED)", "exfiltration: wget with secrets"),
    (r"(?i)cat\s+~/?\.\w*(env|ssh|aws|gnupg|kube)", "exfiltration: read sensitive dotfiles"),
    (r"(?i)cat\s+.*/\.ssh/", "exfiltration: SSH key read"),
    (r"(?i)base64\s+.*\.\w*(env|key|pem|crt)", "exfiltration: base64 encode secrets"),

    # Backdoor
    (r"ssh-keygen\s+.*-f\s*/", "backdoor: SSH key generation"),
    (r"(?i)authorized_keys", "backdoor: SSH authorized_keys manipulation"),
    (r"(?i)crontab\s+-e?\s+.*&&", "backdoor: crontab injection"),

    # Destructive
    (r"rm\s+-rf\s+/(?!\w)", "destructive: root filesystem deletion"),
    (r"(?i)mkfs\s+/dev/", "destructive: filesystem format"),
    (r"(?i)dd\s+if=.*of=/dev/", "destructive: disk overwrite"),
]

# Invisible unicode ranges that could be used to hide content
_INVISIBLE_RANGES = [
    (0x200B, 0x200F),  # Zero-width chars, LTR/RTL marks
    (0x2028, 0x202F),  # Line/paragraph separators, directional controls
    (0x2060, 0x2064),  # Word joiners, invisible operators
    (0xFE00, 0xFE0F),  # Variation selectors
    (0xFFF0, 0xFFFF),  # Specials
    (0xE0000, 0xE007F),  # Tags
]

# Common legitimate unicode that should NOT be flagged
_ALLOWED_UNICODE = frozenset([
    0x00A7,  # §
    0x2014,  # —
    0x2013,  # –
    0x2018,  # '
    0x2019,  # '
    0x201C,  # "
    0x201D,  # "
    0x2026,  # …
    0x00B7,  # ·
    0x2022,  # •
    0x2192,  # →
    0x2190,  # ←
    0x2713,  # ✓
    0x2717,  # ✗
])


def scan_memory_content(content: str) -> ScanResult:
    """Scan memory entry content for injection threats.

    Args:
        content: The text content to scan

    Returns:
        ScanResult with safe=True if clean, or violations list
    """
    violations: list[str] = []

    # Pattern-based detection
    for pattern, reason in _THREAT_PATTERNS:
        if re.search(pattern, content):
            violations.append(reason)

    # Invisible unicode detection
    for ch in content:
        cp = ord(ch)
        if cp in _ALLOWED_UNICODE:
            continue
        for start, end in _INVISIBLE_RANGES:
            if start <= cp <= end:
                violations.append(f"invisible unicode U+{cp:04X} (could hide malicious content)")
                break

    return ScanResult(safe=len(violations) == 0, violations=violations)


def is_safe_memory_content(content: str) -> bool:
    """Quick check if content is safe for memory storage."""
    return scan_memory_content(content).safe


def sanitize_memory_content(content: str) -> str:
    """Remove known dangerous patterns from memory content.

    Note: This is a best-effort sanitizer. For strict security,
    use scan_memory_content() and reject unsafe content entirely.
    """
    # Remove invisible unicode
    cleaned_chars = []
    for ch in content:
        cp = ord(ch)
        is_invisible = False
        if cp not in _ALLOWED_UNICODE:
            for start, end in _INVISIBLE_RANGES:
                if start <= cp <= end:
                    is_invisible = True
                    break
        if not is_invisible:
            cleaned_chars.append(ch)
    return "".join(cleaned_chars)
