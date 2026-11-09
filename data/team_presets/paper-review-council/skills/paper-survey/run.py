#!/usr/bin/env python3
"""Stable entrypoint for the paper-survey team skill.

External agents should run run.sh from the skill directory:

    ./run.sh --all --lite --max-papers 10
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent
CLAWCROSS_ROOT_MARKERS = ("oasis/agent_center.py", "src")
HELP_FLAGS = {"-h", "--help"}
REQUIRED_PACKAGES = {
    "bs4": "beautifulsoup4",
    "openai": "openai",
    "requests": "requests",
    "tqdm": "tqdm",
    "pdfplumber": "pdfplumber",
    "PyPDF2": "PyPDF2",
    "lxml": "lxml",
}


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


def _missing_packages() -> list[str]:
    missing: list[str] = []
    for module_name, package_name in REQUIRED_PACKAGES.items():
        if importlib.util.find_spec(module_name) is None:
            missing.append(package_name)
    return missing


def _ensure_dependencies() -> None:
    missing = _missing_packages()
    if not missing:
        return

    uv_bin = shutil.which("uv")
    if not uv_bin:
        raise RuntimeError(
            "Missing Python dependencies for paper-survey: "
            + ", ".join(missing)
            + ", and uv is not available to install them."
        )

    env = os.environ.copy()
    env.setdefault("UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "clawcross-paper-survey-uv-cache"))

    subprocess.check_call(
        [uv_bin, "pip", "install", "--python", sys.executable, *missing],
        cwd=str(SKILL_DIR),
        env=env,
    )


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

    wants_help = any(arg in HELP_FLAGS for arg in sys.argv[1:])
    if not wants_help:
        _ensure_dependencies()

    from paper_survey import pdf_folder_batch
    from paper_survey.cli import main as cli_main

    if len(sys.argv) > 1 and sys.argv[1] == "pdf-folder":
        sys.argv.pop(1)
        pdf_folder_batch.main()
    else:
        cli_main()


if __name__ == "__main__":
    main()
