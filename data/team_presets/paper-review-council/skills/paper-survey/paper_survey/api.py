"""Small Python API for invoking the packaged paper survey workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

import paper_survey.config as config
from paper_survey.cli import run_full_pipeline, run_from_step
from paper_survey.pdf_folder_batch import DEFAULT_ANALYSIS_PROMPT, run_pdf_folder_batch
from paper_survey.scraper import run_scraper


ConferenceSpec = Tuple[str, int]


def configure(
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    openreview_token: Optional[str] = None,
    output_dir: Optional[str] = None,
    topic: Optional[str] = None,
    filter_prompt_template: Optional[str] = None,
    conferences: Optional[Sequence[ConferenceSpec]] = None,
    lite_mode: Optional[bool] = None,
    max_candidate_papers: Optional[int] = None,
    arxiv_queries: Optional[Iterable[str]] = None,
    arxiv_max_results: Optional[int] = None,
    arxiv_date_from: Optional[str] = None,
) -> None:
    """Apply runtime overrides for Python callers."""
    overrides = {}
    if api_key is not None:
        overrides["api_key"] = api_key
    if base_url is not None:
        overrides["base_url"] = base_url
    if model is not None:
        overrides["model"] = model
    if openreview_token is not None:
        overrides["openreview_token"] = openreview_token
    if output_dir is not None:
        overrides["output_dir"] = str(Path(output_dir).expanduser().resolve())
    if topic is not None:
        overrides["topic"] = topic
    if filter_prompt_template is not None:
        overrides["filter_prompt_template"] = filter_prompt_template
    if conferences is not None:
        overrides["conferences"] = list(conferences)
    if lite_mode is not None:
        overrides["lite_mode"] = lite_mode
    if max_candidate_papers is not None:
        overrides["max_candidate_papers"] = max(0, int(max_candidate_papers))
    if arxiv_queries is not None:
        overrides["arxiv_queries"] = [query for query in arxiv_queries]
    if arxiv_max_results is not None:
        overrides["arxiv_max_results"] = int(arxiv_max_results)
    if arxiv_date_from is not None:
        overrides["arxiv_date_from"] = arxiv_date_from
    if overrides:
        config.override(**overrides)


def scrape_papers():
    config.validate()
    return run_scraper()


def run_pipeline(*, from_step: Optional[str] = None):
    config.validate()
    if from_step:
        return run_from_step(from_step)
    return run_full_pipeline()


def analyze_pdf_folder(
    input_dir: str,
    *,
    output_dir: Optional[str] = None,
    recursive: bool = False,
    overwrite: bool = False,
    workers: Optional[int] = None,
    chunk_chars: int = 45000,
    analysis_prompt: Optional[str] = None,
):
    config.validate()
    input_path = Path(input_dir).expanduser().resolve()
    out_path = Path(output_dir).expanduser().resolve() if output_dir else input_path / "analysis_reports"
    return run_pdf_folder_batch(
        input_dir=input_path,
        output_dir=out_path,
        recursive=recursive,
        overwrite=overwrite,
        workers=max(1, workers or config.MAX_CONCURRENT_ANALYSIS),
        chunk_chars=max(1000, chunk_chars),
        analysis_prompt=analysis_prompt or DEFAULT_ANALYSIS_PROMPT,
    )
