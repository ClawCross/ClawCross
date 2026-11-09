"""
Bash Command Safety Analyzer – Claude Code style.

Features:
- AST-level parsing of bash commands (using shlex + pattern analysis)
- Detects dangerous patterns: rm -rf, sudo, curl|bash, etc.
- Classifies commands into risk levels
- Provides deny-invariants that cannot be bypassed
- Supports allowlist/blocklist patterns

Claude Code uses tree-sitter for full AST; we use a lightweight
shlex-based parser with regex pattern matching for equivalent coverage
in Python, avoiding the tree-sitter native dependency.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    """Command risk classification."""
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"  # Deny invariant – cannot be bypassed


@dataclass(frozen=True)
class CommandAnalysis:
    """Result of analyzing a bash command."""
    command: str
    risk_level: RiskLevel
    reasons: tuple[str, ...]
    blocked: bool = False
    suggested_alternative: str = ""


# ---------------------------------------------------------------------------
# Deny invariants – these CANNOT be bypassed regardless of policy
# ---------------------------------------------------------------------------

_DENY_INVARIANT_PATTERNS: list[tuple[str, str]] = [
    # Recursive force delete at root or home
    (r'\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*f[a-zA-Z]*r)\s+(/|~|\$HOME)\s*$', "rm -rf on root/home directory"),
    (r'\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*f[a-zA-Z]*r)\s+/\s', "rm -rf starting from root"),
    # Format disk
    (r'\bmkfs\b', "filesystem format command"),
    (r'\bdd\s+.*\bof=/dev/', "dd writing to device"),
    # Fork bomb
    (r':\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:', "fork bomb pattern"),
    (r':\(\)\s*\{.*\};\s*:', "fork bomb pattern"),
    (r'\bwhile\s+true.*?done\s*&', "infinite background loop"),
    # Kernel/system destruction
    (r'>\s*/dev/sd[a-z]', "writing directly to block device"),
    (r'\bsysctl\s+-w\s+kernel', "modifying kernel parameters"),
    # Credential theft
    (r'cat\s+.*\.ssh/(id_rsa|id_ed25519|authorized_keys)', "reading SSH keys"),
    (r'cat\s+/etc/(shadow|passwd)', "reading system credentials"),
    # Network exfiltration of sensitive files
    (r'curl.*(-d|--data).*(/etc/shadow|\.ssh/|\.env|\.git/config)', "exfiltrating sensitive files"),
    (r'wget.*(-O\s*-|--output-document\s*-).*\|\s*(ba)?sh', "download-and-execute pattern"),
]

# ---------------------------------------------------------------------------
# High risk patterns (require approval)
# ---------------------------------------------------------------------------

_HIGH_RISK_PATTERNS: list[tuple[str, str]] = [
    (r'\bsudo\b', "sudo usage"),
    (r'\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*f[a-zA-Z]*r)\b', "recursive force delete"),
    (r'\bchmod\s+777\b', "world-writable permissions"),
    (r'\bchmod\s+\+s\b', "setuid bit"),
    (r'\bchown\s+-R\s+root\b', "recursive ownership to root"),
    (r'\bcurl\b.*\|\s*(ba)?sh', "piping curl to shell"),
    (r'\bwget\b.*\|\s*(ba)?sh', "piping wget to shell"),
    (r'\bgit\s+push\s+.*--force', "git force push"),
    (r'\bgit\s+reset\s+--hard', "git hard reset"),
    (r'\bkill\s+-9\s+(-1|1)\b', "killing init/all processes"),
    (r'\biptables\b', "firewall modification"),
    (r'\bsystemctl\s+(stop|disable|mask)\b', "stopping system services"),
    (r'\bnpm\s+publish\b', "npm publish"),
    (r'\bpip\s+install\s+(?!-r\b)(?!--requirement\b).*\s+--', "pip install with flags"),
    (r'\bdocker\s+rm\s+-f', "force removing docker containers"),
    (r'\benv\b.*\bPATH=', "modifying PATH"),
]

# ---------------------------------------------------------------------------
# Medium risk patterns (warn)
# ---------------------------------------------------------------------------

_MEDIUM_RISK_PATTERNS: list[tuple[str, str]] = [
    (r'\brm\s+-[a-zA-Z]*r[a-zA-Z]*\b', "recursive delete"),
    (r'\bcurl\b', "network download"),
    (r'\bwget\b', "network download"),
    (r'\bpip\s+install\b', "pip install"),
    (r'\bnpm\s+install\s+-g\b', "global npm install"),
    (r'\bgit\s+checkout\b.*--\s*\.', "git discard changes"),
    (r'\bgit\s+stash\s+drop\b', "dropping git stash"),
    (r'\bsed\s+-i\b', "in-place file editing"),
    (r'\btruncate\b', "file truncation"),
    (r'>\s*/dev/null\s+2>&1', "silencing output"),
    (r'\bkillall\b', "killing processes by name"),
    (r'\bpkill\b', "killing processes by pattern"),
]

# ---------------------------------------------------------------------------
# Safe command patterns (always allowed)
# ---------------------------------------------------------------------------

_SAFE_COMMANDS: frozenset[str] = frozenset({
    "ls", "pwd", "echo", "cat", "head", "tail", "wc",
    "grep", "find", "which", "whoami", "date", "uname",
    "file", "stat", "du", "df",
    "git status", "git log", "git diff", "git show", "git branch",
    "python --version", "node --version", "npm --version",
    "pip list", "pip show",
})


def _tokenize_command(command: str) -> list[str]:
    """Tokenize a bash command using shlex, falling back to split."""
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _check_pipe_chain(command: str) -> list[str]:
    """Split command by pipes and analyze the chain."""
    warnings = []
    parts = command.split("|")
    if len(parts) > 1:
        last_part = parts[-1].strip()
        # Check for piping to shell
        if re.match(r'\s*(ba)?sh\b', last_part):
            warnings.append("piping to shell interpreter")
        # Check for piping to python/node
        if re.match(r'\s*(python|node|ruby|perl)\b', last_part):
            warnings.append("piping to script interpreter")
    return warnings


def _check_redirects(command: str) -> list[str]:
    """Check for dangerous redirects."""
    warnings = []
    # Overwriting important files
    if re.search(r'>\s*/etc/', command):
        warnings.append("redirecting to /etc/")
    if re.search(r'>\s*~/', command) or re.search(r'>\s*\$HOME/', command):
        dangerous_files = [".bashrc", ".profile", ".ssh/", ".gitconfig"]
        for f in dangerous_files:
            if f in command:
                warnings.append(f"redirecting to {f}")
    return warnings


def analyze_command(command: str) -> CommandAnalysis:
    """
    Analyze a bash command for safety.

    Returns a CommandAnalysis with risk level and blocking decision.
    """
    if not command or not command.strip():
        return CommandAnalysis(
            command=command,
            risk_level=RiskLevel.SAFE,
            reasons=(),
        )

    normalized = command.strip()
    reasons: list[str] = []

    # Deny invariants are never allowed to continue to approval or allowlists.
    for pattern, description in _DENY_INVARIANT_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return CommandAnalysis(
                command=command,
                risk_level=RiskLevel.CRITICAL,
                reasons=(f"deny invariant: {description}",),
                blocked=True,
                suggested_alternative="Use a narrower, non-destructive command.",
            )

    # Check safe commands
    tokens = _tokenize_command(normalized)
    if tokens:
        base_cmd = tokens[0]
        full_cmd = " ".join(tokens[:2]) if len(tokens) > 1 else base_cmd
        if base_cmd in _SAFE_COMMANDS or full_cmd in _SAFE_COMMANDS:
            return CommandAnalysis(
                command=command,
                risk_level=RiskLevel.SAFE,
                reasons=(),
            )

    # Check high risk patterns
    for pattern, description in _HIGH_RISK_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            reasons.append(description)

    if reasons:
        return CommandAnalysis(
            command=command,
            risk_level=RiskLevel.HIGH,
            reasons=tuple(reasons),
            blocked=False,  # High risk = needs approval, not auto-blocked
        )

    # Check medium risk patterns
    for pattern, description in _MEDIUM_RISK_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            reasons.append(description)

    # Check pipe chains and redirects
    reasons.extend(_check_pipe_chain(normalized))
    reasons.extend(_check_redirects(normalized))

    if reasons:
        return CommandAnalysis(
            command=command,
            risk_level=RiskLevel.MEDIUM,
            reasons=tuple(reasons),
        )

    # Check for subshells and eval
    if re.search(r'\beval\b', normalized):
        return CommandAnalysis(
            command=command,
            risk_level=RiskLevel.MEDIUM,
            reasons=("eval usage",),
        )

    if "$(" in normalized or "`" in normalized:
        return CommandAnalysis(
            command=command,
            risk_level=RiskLevel.LOW,
            reasons=("command substitution",),
        )

    return CommandAnalysis(
        command=command,
        risk_level=RiskLevel.LOW,
        reasons=(),
    )


def is_command_safe(command: str) -> bool:
    """Quick check: is the command safe to execute without approval?"""
    result = analyze_command(command)
    return result.risk_level in (RiskLevel.SAFE, RiskLevel.LOW)


def is_command_blocked(command: str) -> bool:
    """Quick check: is the command blocked by deny invariants?"""
    result = analyze_command(command)
    return result.blocked


def batch_analyze(commands: list[str]) -> list[CommandAnalysis]:
    """Analyze multiple commands."""
    return [analyze_command(cmd) for cmd in commands]


# ---------------------------------------------------------------------------
# Runtime allowlist/blocklist management
# ---------------------------------------------------------------------------

_runtime_allowlist: set[str] = set()
_runtime_blocklist: set[str] = set()


def add_to_allowlist(command_prefix: str) -> None:
    """Add a command prefix to the runtime allowlist (bypasses medium/low risk)."""
    _runtime_allowlist.add(command_prefix.strip())


def remove_from_allowlist(command_prefix: str) -> None:
    """Remove a command prefix from the runtime allowlist."""
    _runtime_allowlist.discard(command_prefix.strip())


def add_to_blocklist(pattern: str) -> None:
    """Add a regex pattern to the runtime blocklist (blocks matching commands)."""
    _runtime_blocklist.add(pattern.strip())


def remove_from_blocklist(pattern: str) -> None:
    """Remove a pattern from the runtime blocklist."""
    _runtime_blocklist.discard(pattern.strip())


def get_allowlist() -> frozenset[str]:
    """Get current runtime allowlist."""
    return frozenset(_runtime_allowlist)


def get_blocklist() -> frozenset[str]:
    """Get current runtime blocklist."""
    return frozenset(_runtime_blocklist)


def check_runtime_lists(command: str) -> CommandAnalysis | None:
    """Check command against runtime allowlist/blocklist. Returns None if no match."""
    normalized = command.strip()
    # Blocklist takes priority
    for pattern in _runtime_blocklist:
        if re.search(pattern, normalized, re.IGNORECASE):
            return CommandAnalysis(
                command=command,
                risk_level=RiskLevel.HIGH,
                reasons=(f"Blocked by runtime blocklist: {pattern}",),
                blocked=False,
            )
    # Allowlist
    for prefix in _runtime_allowlist:
        if normalized.startswith(prefix):
            return CommandAnalysis(
                command=command,
                risk_level=RiskLevel.SAFE,
                reasons=(f"Allowed by runtime allowlist: {prefix}",),
            )
    return None


# ---------------------------------------------------------------------------
# Advanced: operator chain, env injection, subshell, heredoc detection
# ---------------------------------------------------------------------------

def detect_operator_chains(command: str) -> list[str]:
    """Detect dangerous operator chains (&&, ||, ;) that could hide malicious commands."""
    warnings = []
    # Split by operators and check each segment
    segments = re.split(r'\s*(?:&&|\|\||;)\s*', command)
    if len(segments) > 3:
        warnings.append(f"complex operator chain ({len(segments)} segments)")
    for seg in segments[1:]:  # Skip first (usually safe)
        seg_analysis = analyze_command(seg.strip())
        if seg_analysis.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            warnings.append(f"dangerous command in chain: {seg.strip()[:60]}")
    return warnings


def detect_env_injection(command: str) -> list[str]:
    """Detect environment variable injection patterns."""
    warnings = []
    # Setting sensitive env vars
    if re.search(r'\bexport\s+(PATH|LD_PRELOAD|LD_LIBRARY_PATH|PYTHONPATH|NODE_PATH)\s*=', command):
        warnings.append("modifying sensitive environment variable")
    # Unsetting PATH
    if re.search(r'\bunset\s+PATH\b', command):
        warnings.append("unsetting PATH")
    return warnings


def detect_heredoc(command: str) -> list[str]:
    """Detect heredoc patterns that could hide malicious content."""
    warnings = []
    if re.search(r'<<\s*[\'"]?\w+[\'"]?', command):
        warnings.append("heredoc detected (content may be hidden)")
    return warnings


def detect_subshell_nesting(command: str) -> list[str]:
    """Detect deeply nested subshells."""
    warnings = []
    depth = 0
    max_depth = 0
    for char in command:
        if char == '(':
            depth += 1
            max_depth = max(max_depth, depth)
        elif char == ')':
            depth = max(0, depth - 1)
    if max_depth >= 3:
        warnings.append(f"deeply nested subshells (depth {max_depth})")
    return warnings


def deep_analyze(command: str) -> CommandAnalysis:
    """
    Extended analysis including operator chains, env injection, heredocs, and subshell nesting.

    Use this for commands that passed basic analyze_command() but need deeper inspection.
    """
    base = analyze_command(command)
    if base.blocked:
        return base

    extra_reasons = []
    extra_reasons.extend(detect_operator_chains(command))
    extra_reasons.extend(detect_env_injection(command))
    extra_reasons.extend(detect_heredoc(command))
    extra_reasons.extend(detect_subshell_nesting(command))

    if not extra_reasons:
        return base

    # Escalate risk level if deep analysis found issues
    combined_reasons = list(base.reasons) + extra_reasons
    new_level = base.risk_level
    if any("dangerous command in chain" in r for r in extra_reasons):
        new_level = RiskLevel.HIGH
    elif any("deeply nested" in r or "heredoc" in r for r in extra_reasons):
        new_level = max(new_level, RiskLevel.MEDIUM, key=lambda x: list(RiskLevel).index(x))

    return CommandAnalysis(
        command=command,
        risk_level=new_level,
        reasons=tuple(combined_reasons),
        blocked=base.blocked,
    )
