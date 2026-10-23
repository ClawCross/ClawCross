"""Compatibility entry: all terminal commands use the same Python CLI."""
from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    from src.cli.clawcross import main as unified_main
    return unified_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
