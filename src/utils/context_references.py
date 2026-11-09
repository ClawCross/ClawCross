"""
Context References — @-syntax for inline context injection.

Ported from Hermes Agent's context_references:
- @file:path/to/file — Include file contents
- @file:path/to/file:10-20 — Specific line range
- @folder:path/to/dir — Folder listing
- @diff — git diff
- @staged — git diff --staged
- @git:N — Last N commits with patches
- @url:https://... — Fetch and extract web content (placeholder)

Security:
- Blocks sensitive paths (.ssh, .env, credentials)
- Hard limit: 50% of context window
- Path traversal prevention
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ContextReferenceResult:
    """Result of expanding context references."""
    expanded_message: str
    injected_content: str
    references_found: int
    references_expanded: int
    warnings: list[str] = field(default_factory=list)
    estimated_tokens: int = 0


# Regex to match @-references
_REF_PATTERN = re.compile(
    r"@(file|folder|diff|staged|git|url)"
    r"(?::([^\s]+))?"
)

# Sensitive paths that should never be exposed
_SENSITIVE_PATHS = {
    ".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure",
    ".config/gh", ".config/gcloud",
}

_SENSITIVE_FILES = {
    ".env", ".netrc", ".pgpass", ".npmrc", ".pypirc",
    "id_rsa", "id_ed25519", "id_ecdsa", "credentials.json",
    "service_account.json", "token.json",
}


def parse_context_references(message: str) -> list[tuple[str, str]]:
    """Extract all @-references from a message.

    Returns:
        List of (ref_type, ref_arg) tuples
    """
    refs: list[tuple[str, str]] = []
    for match in _REF_PATTERN.finditer(message):
        ref_type = match.group(1)
        ref_arg = match.group(2) or ""
        refs.append((ref_type, ref_arg))
    return refs


def expand_context_references(
    message: str,
    *,
    cwd: str | Path = "",
    context_limit: int = 128000,
    allowed_root: str | Path = "",
) -> ContextReferenceResult:
    """Expand @-references in a user message with actual content.

    Args:
        message: Raw user message with @-references
        cwd: Current working directory for relative paths
        context_limit: Max context tokens (for limit calculation)
        allowed_root: Root directory for path restriction

    Returns:
        ContextReferenceResult with expanded message and metadata
    """
    refs = parse_context_references(message)
    if not refs:
        return ContextReferenceResult(
            expanded_message=message,
            injected_content="",
            references_found=0,
            references_expanded=0,
        )

    cwd = Path(cwd) if cwd else Path.cwd()
    allowed_root = Path(allowed_root) if allowed_root else cwd
    hard_limit = context_limit // 2  # 50% of context window
    soft_limit = context_limit // 4  # 25% warning threshold

    injected_parts: list[str] = []
    warnings: list[str] = []
    expanded_count = 0
    total_chars = 0

    expanded_message = message
    for ref_type, ref_arg in refs:
        if total_chars >= hard_limit * 4:  # Rough char-to-token ratio
            warnings.append(f"Context limit reached, skipping remaining references")
            break

        try:
            content = _expand_reference(ref_type, ref_arg, cwd, allowed_root)
            if content:
                injected_parts.append(content)
                total_chars += len(content)
                expanded_count += 1

                if total_chars > soft_limit * 4:
                    warnings.append(f"Context references exceed 25% of context window")
        except SecurityError as e:
            warnings.append(f"Blocked: {e}")
        except Exception as e:
            warnings.append(f"Failed to expand @{ref_type}:{ref_arg}: {e}")

    # Build expanded message: original message + injected context
    injected_content = ""
    if injected_parts:
        injected_content = "\n\n---\n**Injected Context:**\n\n" + "\n\n".join(injected_parts)

    return ContextReferenceResult(
        expanded_message=message + injected_content if injected_parts else message,
        injected_content=injected_content,
        references_found=len(refs),
        references_expanded=expanded_count,
        warnings=warnings,
        estimated_tokens=total_chars // 4,
    )


class SecurityError(Exception):
    """Raised when a reference targets a sensitive path."""
    pass


def _expand_reference(
    ref_type: str,
    ref_arg: str,
    cwd: Path,
    allowed_root: Path,
) -> str:
    """Dispatch to the appropriate reference expander."""
    if ref_type == "file":
        return _expand_file(ref_arg, cwd, allowed_root)
    elif ref_type == "folder":
        return _expand_folder(ref_arg, cwd, allowed_root)
    elif ref_type == "diff":
        return _expand_git_diff(cwd)
    elif ref_type == "staged":
        return _expand_git_staged(cwd)
    elif ref_type == "git":
        return _expand_git_log(ref_arg, cwd)
    elif ref_type == "url":
        return _expand_url(ref_arg)
    return ""


def _ensure_safe_path(path: Path, allowed_root: Path) -> Path:
    """Validate that a path is safe to access."""
    resolved = path.resolve()

    # Check for path traversal
    if ".." in str(path):
        raise SecurityError(f"Path traversal detected: {path}")

    # Check sensitive directories
    for part in resolved.parts:
        if part in _SENSITIVE_PATHS:
            raise SecurityError(f"Sensitive path blocked: {part}")

    # Check sensitive files
    if resolved.name in _SENSITIVE_FILES:
        raise SecurityError(f"Sensitive file blocked: {resolved.name}")

    # Ensure within allowed root (if set)
    try:
        resolved.relative_to(allowed_root.resolve())
    except ValueError:
        raise SecurityError(f"Path outside allowed root: {path}")

    return resolved


def _expand_file(ref_arg: str, cwd: Path, allowed_root: Path) -> str:
    """Expand @file:path[:line_range]."""
    if not ref_arg:
        return ""

    # Parse optional line range (e.g., file.py:10-20)
    line_start = None
    line_end = None
    parts = ref_arg.rsplit(":", 1)
    if len(parts) == 2 and re.match(r"\d+-\d+$", parts[1]):
        ref_arg = parts[0]
        range_parts = parts[1].split("-")
        line_start = int(range_parts[0])
        line_end = int(range_parts[1])

    path = cwd / ref_arg
    path = _ensure_safe_path(path, allowed_root)

    if not path.is_file():
        return f"[File not found: {ref_arg}]"

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    if line_start is not None and line_end is not None:
        lines = lines[max(0, line_start - 1):line_end]
        range_str = f" (lines {line_start}-{line_end})"
    else:
        range_str = ""

    # Cap at 5000 lines
    if len(lines) > 5000:
        lines = lines[:5000]
        lines.append(f"[... truncated at 5000 lines]")

    header = f"**@file:{ref_arg}{range_str}**"
    return f"{header}\n```\n" + "\n".join(lines) + "\n```"


def _expand_folder(ref_arg: str, cwd: Path, allowed_root: Path) -> str:
    """Expand @folder:path — directory tree listing."""
    if not ref_arg:
        return ""

    path = cwd / ref_arg
    path = _ensure_safe_path(path, allowed_root)

    if not path.is_dir():
        return f"[Directory not found: {ref_arg}]"

    lines = [f"**@folder:{ref_arg}**", "```"]
    _build_tree(path, lines, prefix="", max_depth=4, max_items=200)
    lines.append("```")
    return "\n".join(lines)


def _build_tree(
    path: Path,
    lines: list[str],
    prefix: str,
    max_depth: int,
    max_items: int,
    current_depth: int = 0,
) -> int:
    """Build a tree listing recursively."""
    if current_depth >= max_depth or len(lines) >= max_items:
        return len(lines)

    items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    for i, item in enumerate(items):
        if len(lines) >= max_items:
            lines.append(f"{prefix}... ({len(items) - i} more)")
            break
        is_last = (i == len(items) - 1)
        connector = "└── " if is_last else "├── "
        suffix = "/" if item.is_dir() else ""
        lines.append(f"{prefix}{connector}{item.name}{suffix}")
        if item.is_dir():
            extension = "    " if is_last else "│   "
            _build_tree(item, lines, prefix + extension, max_depth, max_items, current_depth + 1)

    return len(lines)


def _expand_git_diff(cwd: Path) -> str:
    """Expand @diff — git diff."""
    try:
        result = subprocess.run(
            ["git", "diff"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        diff = result.stdout.strip()
        if not diff:
            return "**@diff** — No unstaged changes"
        # Cap at 50K chars
        if len(diff) > 50000:
            diff = diff[:50000] + "\n[... truncated]"
        return f"**@diff**\n```diff\n{diff}\n```"
    except Exception as e:
        return f"[git diff failed: {e}]"


def _expand_git_staged(cwd: Path) -> str:
    """Expand @staged — git diff --staged."""
    try:
        result = subprocess.run(
            ["git", "diff", "--staged"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        diff = result.stdout.strip()
        if not diff:
            return "**@staged** — No staged changes"
        if len(diff) > 50000:
            diff = diff[:50000] + "\n[... truncated]"
        return f"**@staged**\n```diff\n{diff}\n```"
    except Exception as e:
        return f"[git diff --staged failed: {e}]"


def _expand_git_log(ref_arg: str, cwd: Path) -> str:
    """Expand @git:N — last N commits with patches."""
    try:
        n = min(int(ref_arg or "3"), 10)
    except ValueError:
        n = 3

    try:
        result = subprocess.run(
            ["git", "log", f"-{n}", "--oneline", "-p"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
        )
        log = result.stdout.strip()
        if not log:
            return "**@git** — No commits found"
        if len(log) > 80000:
            log = log[:80000] + "\n[... truncated]"
        return f"**@git:{n}**\n```\n{log}\n```"
    except Exception as e:
        return f"[git log failed: {e}]"


def _expand_url(ref_arg: str) -> str:
    """Expand @url:https://... — placeholder for URL fetching."""
    if not ref_arg:
        return ""
    # URL fetching requires external tools; return a placeholder
    return f"**@url:{ref_arg}** — [URL content injection requires web_search tool. Use web_search(\"{ref_arg}\") instead.]"
