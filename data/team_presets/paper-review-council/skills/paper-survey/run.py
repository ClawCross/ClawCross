#!/usr/bin/env python3
"""Stable entrypoint for the paper-survey team skill.

External agents should run run.sh from the skill directory:

    ./run.sh --all --lite --max-papers 10
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent
CLAWCROSS_ROOT_MARKERS = ("oasis/agent_center.py", "src")


def _find_clawcross_root(start: Path) -> Path | None:
    for path in (start, *start.parents):
        if (path / CLAWCROSS_ROOT_MARKERS[0]).exists() and (path / CLAWCROSS_ROOT_MARKERS[1]).exists():
            return path
    return None


def _prepare_imports() -> None:
    clawcross_root = _find_clawcross_root(SKILL_DIR)
    for path in (SKILL_DIR, clawcross_root):
        if path is None:
            continue
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def main() -> None:
    _prepare_imports()

    if len(sys.argv) > 1 and sys.argv[1] == "inspect-output":
        output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output")
        all_papers = output_dir / "all_papers_raw.json"
        paper_list = output_dir / "paper_list.json"
        survey = output_dir / "survey_report.md"
        print("all_papers_raw", len(json.loads(all_papers.read_text())) if all_papers.exists() else "missing")
        print("paper_list", len(json.loads(paper_list.read_text())) if paper_list.exists() else "missing")
        print("survey_exists", survey.exists(), survey.stat().st_size if survey.exists() else 0)
        return

    from paper_survey import pdf_folder_batch
    from paper_survey.cli import main as cli_main

    if len(sys.argv) > 1 and sys.argv[1] == "pdf-folder":
        sys.argv.pop(1)
        pdf_folder_batch.main()
    else:
        cli_main()


if __name__ == "__main__":
    main()
