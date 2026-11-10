"""Curses-based interactive picker for ClawCross.

Adapted from Hermes' hermes_cli/curses_ui.py — provides a single-select
radio list with a numbered text fallback for non-curses terminals.
Color dependencies are stripped; plain text only.
"""

from __future__ import annotations

import sys
import unicodedata
from typing import List


def flush_stdin() -> None:
    """Flush any stray bytes from the stdin input buffer.

    Must be called after ``curses.wrapper()`` returns, **before** the next
    ``input()`` / ``getpass.getpass()`` call.  ``curses.endwin()`` restores the
    terminal but does NOT drain the OS input buffer — leftover escape-sequence
    bytes (from arrow keys, terminal mode-switch responses, or rapid keypresses)
    remain buffered and silently get consumed by the next ``input()`` call,
    corrupting user data (e.g. writing ``^[^[`` into .env files).

    On non-TTY stdin (piped, redirected) or Windows, this is a no-op.
    """
    try:
        if not sys.stdin.isatty():
            return
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass


def _display_width(text: str) -> int:
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1
    return width


def _fit_display(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    out: list[str] = []
    used = 0
    for ch in text:
        ch_width = 2 if unicodedata.east_asian_width(ch) in {"F", "W"} else 1
        if used + ch_width > max_width:
            break
        out.append(ch)
        used += ch_width
    return "".join(out)


def _add_display_str(stdscr, y: int, x: int, text: str, max_width: int, attr=0) -> None:
    try:
        stdscr.addstr(y, x, _fit_display(text, max_width), attr)
    except Exception:
        pass


def curses_radiolist(
    title: str,
    items: List[str],
    selected: int = 0,
    *,
    cancel_returns: int | None = None,
    description: str | None = None,
) -> int:
    """Curses single-select radio list. Returns the selected index.

    Args:
        title: Header line displayed above the list.
        items: Display labels for each row.
        selected: Index that starts selected (pre-selected).
        cancel_returns: Returned on ESC/q. Defaults to the original *selected*.
        description: Optional multi-line text shown between the title and
            the item list.
    """
    if cancel_returns is None:
        cancel_returns = selected

    if not items:
        return cancel_returns

    if not sys.stdin.isatty():
        return cancel_returns

    desc_lines: list[str] = []
    if description:
        desc_lines = description.splitlines()

    try:
        import curses
        result_holder: list = [None]

        def _draw(stdscr):
            curses.curs_set(0)
            stdscr.keypad(True)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
                curses.init_pair(3, 8, -1)  # dim gray for footer
            cursor = max(0, min(selected, len(items) - 1))
            scroll_offset = 0

            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()

                row = 0

                # Header
                try:
                    hattr = curses.A_BOLD
                    if curses.has_colors():
                        hattr |= curses.color_pair(2)
                    _add_display_str(stdscr, row, 0, title, max_x - 1, hattr)
                    row += 1

                    for dline in desc_lines:
                        if row >= max_y - 2:
                            break
                        _add_display_str(stdscr, row, 0, dline, max_x - 1, curses.A_NORMAL)
                        row += 1

                    _add_display_str(
                        stdscr, row, 0,
                        "  ↑↓ move  PgUp/PgDn page  Home/End jump  ENTER select  ESC cancel",
                        max_x - 1, curses.A_DIM,
                    )
                    row += 1
                except curses.error:
                    pass

                # Reserve last row for status line
                items_start = row + 1
                visible_rows = max(1, max_y - items_start - 1)
                if cursor < scroll_offset:
                    scroll_offset = cursor
                elif cursor >= scroll_offset + visible_rows:
                    scroll_offset = cursor - visible_rows + 1
                # Clamp scroll_offset so we don't show empty bottom rows
                max_scroll = max(0, len(items) - visible_rows)
                if scroll_offset > max_scroll:
                    scroll_offset = max_scroll

                for draw_i, i in enumerate(
                    range(scroll_offset, min(len(items), scroll_offset + visible_rows))
                ):
                    y = draw_i + items_start
                    if y >= max_y - 1:
                        break
                    radio = "●" if i == selected else "○"
                    arrow = "→" if i == cursor else " "
                    line = f" {arrow} ({radio}) {items[i]}"
                    attr = curses.A_NORMAL
                    if i == cursor:
                        attr = curses.A_BOLD
                        if curses.has_colors():
                            attr |= curses.color_pair(1)
                    _add_display_str(stdscr, y, 0, line, max_x - 1, attr)

                # Bottom status: "N/M" + scroll indicator
                try:
                    sattr = curses.A_DIM
                    if curses.has_colors():
                        sattr |= curses.color_pair(3)
                    pos = f" {cursor + 1}/{len(items)} "
                    if scroll_offset > 0 and (scroll_offset + visible_rows) < len(items):
                        marker = "↕"
                    elif scroll_offset > 0:
                        marker = "↑"
                    elif (scroll_offset + visible_rows) < len(items):
                        marker = "↓"
                    else:
                        marker = " "
                    status = f"{pos}{marker}"
                    sx = max(0, max_x - _display_width(status) - 1)
                    _add_display_str(stdscr, max_y - 1, sx, status, max_x - sx - 1, sattr)
                except curses.error:
                    pass

                stdscr.refresh()
                key = stdscr.getch()

                if key in (curses.KEY_UP, ord("k")):
                    cursor = max(0, cursor - 1)
                elif key in (curses.KEY_DOWN, ord("j")):
                    cursor = min(len(items) - 1, cursor + 1)
                elif key in (curses.KEY_PPAGE,):  # PgUp
                    cursor = max(0, cursor - visible_rows)
                elif key in (curses.KEY_NPAGE,):  # PgDn
                    cursor = min(len(items) - 1, cursor + visible_rows)
                elif key in (curses.KEY_HOME, ord("g")):
                    cursor = 0
                elif key in (curses.KEY_END, ord("G")):
                    cursor = len(items) - 1
                elif key in (ord(" "), curses.KEY_ENTER, 10, 13):
                    result_holder[0] = cursor
                    return
                elif key in (27, ord("q")):
                    result_holder[0] = cancel_returns
                    return

        curses.wrapper(_draw)
        flush_stdin()
        return result_holder[0] if result_holder[0] is not None else cancel_returns

    except KeyboardInterrupt:
        return cancel_returns
    except Exception:
        return _radio_numbered_fallback(title, items, selected, cancel_returns)


def _radio_numbered_fallback(
    title: str,
    items: List[str],
    selected: int,
    cancel_returns: int,
) -> int:
    """Text-based numbered fallback for radio selection."""
    print(f"\n  {title}")
    print("  Select by number, Enter to confirm.\n")

    for i, label in enumerate(items):
        marker = "(●)" if i == selected else "(○)"
        print(f"  {marker} {i + 1:>2}. {label}")
    print()
    try:
        val = input(f"  Choice [default {selected + 1}]: ").strip()
        if not val:
            return selected
        idx = int(val) - 1
        if 0 <= idx < len(items):
            return idx
        return selected
    except (ValueError, KeyboardInterrupt, EOFError):
        return cancel_returns


def prompt_text(label: str, *, default: str = "") -> str:
    """Print *label* and return the user's input.

    On non-tty stdin, returns *default* immediately so non-interactive
    invocations don't hang.
    """
    if not sys.stdin.isatty():
        return default
    try:
        val = input(label).strip()
    except (KeyboardInterrupt, EOFError):
        return default
    return val or default
