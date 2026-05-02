#!/usr/bin/env python3
"""
paper-survey CLI - automated academic paper survey pipeline.

Primary runtime settings are loaded from the project-level `runtime_config.json`.
CLI flags can override selected settings for a single run.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime

import paper_survey.config as config
from paper_survey.config import OUTPUT_DIR, PAPER_LIST_FILE, SURVEY_FILE, LOGS_DIR
from paper_survey.llm import get_llm_stats, reset_llm_stats


def _setup_logging():
    """Configure logging with file + console handlers."""
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                os.path.join(config.LOGS_DIR, f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
                encoding='utf-8'
            ),
        ]
    )


def step_scrape():
    """Step 1: Scrape papers."""
    print(f"\n{'='*80}")
    print("STEP 1: SCRAPING PAPERS FROM CONFERENCES")
    print(f"{'='*80}\n")
    from paper_survey.scraper import run_scraper
    return run_scraper()


def step_download(papers=None):
    """Step 2: Download PDFs."""
    print(f"\n{'='*80}")
    print("STEP 2: DOWNLOADING PDF FILES")
    print(f"{'='*80}\n")
    from paper_survey.downloader import download_all_papers
    return download_all_papers(papers)


def step_analyze(papers=None):
    """Step 3: Analyze papers with LLM."""
    print(f"\n{'='*80}")
    print("STEP 3: ANALYZING PAPERS WITH LLM")
    print(f"{'='*80}\n")
    from paper_survey.analyzer import run_analysis
    return run_analysis(papers)


def step_survey(papers=None):
    """Step 4: Generate survey."""
    print(f"\n{'='*80}")
    print("STEP 4: GENERATING SURVEY REPORT")
    print(f"{'='*80}\n")
    from paper_survey.survey_generator import generate_survey
    return generate_survey(papers)


def run_full_pipeline():
    """Run the complete pipeline."""
    start_time = time.time()
    reset_llm_stats()

    mode_str = "LITE" if config.LITE_MODE else "FULL"
    print(f"\n{'#'*80}")
    print(f"#  PAPER SURVEY PIPELINE [{mode_str} MODE]")
    print(f"#  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*80}")

    if config.LITE_MODE:
        print("\n  Lite mode: skip PDF download, use abstracts only, concise survey.\n")

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    os.makedirs(config.LOGS_DIR, exist_ok=True)

    # Step 1: Scrape
    papers = step_scrape()

    if not papers:
        print("\n  No papers found! Check your configuration and network connection.")
        print("You can also manually create a paper_list.json and skip to analyze step.")
        return

    # Step 2: Download (skip in lite mode)
    if config.LITE_MODE:
        print(f"\n{'='*80}")
        print("STEP 2: SKIPPED (Lite mode - no PDF download)")
        print(f"{'='*80}\n")
    else:
        papers = step_download(papers)

    # Step 3: Analyze
    papers = step_analyze(papers)

    # Step 4: Survey
    step_survey(papers)

    elapsed = time.time() - start_time
    llm_stats = get_llm_stats()
    print(f"\n{'#'*80}")
    print(f"# PIPELINE COMPLETE [{mode_str} MODE]")
    print(f"# Total time: {elapsed/60:.1f} minutes")
    print(f"# Survey saved to: {config.SURVEY_FILE}")
    print(f"# LLM calls: {llm_stats['calls']} | total tokens: {llm_stats['total_tokens']}")
    print(f"{'#'*80}")


def run_from_step(step_name: str):
    """Run pipeline starting from a specific step."""
    if config.LITE_MODE:
        steps = ["scrape", "analyze", "survey"]
    else:
        steps = ["scrape", "download", "analyze", "survey"]

    if step_name not in steps:
        if step_name == "download" and config.LITE_MODE:
            print("Download step is skipped in lite mode. Starting from analyze.")
            step_name = "analyze"
        else:
            print(f"Unknown step: {step_name}. Available: {steps}")
            return

    start_idx = steps.index(step_name)
    papers = None

    if start_idx > 0 and os.path.exists(config.PAPER_LIST_FILE):
        with open(config.PAPER_LIST_FILE, 'r', encoding='utf-8') as f:
            papers = json.load(f)
        print(f"Loaded {len(papers)} papers from {config.PAPER_LIST_FILE}")

    step_func_map = {
        "scrape": step_scrape,
        "download": step_download,
        "analyze": step_analyze,
        "survey": step_survey,
    }
    for i in range(start_idx, len(steps)):
        step = steps[i]
        func = step_func_map[step]
        if step == "scrape":
            papers = func()
        elif step == "survey":
            func(papers)
        else:
            papers = func(papers)


def _parse_conferences(value: str):
    """Parse conference string like 'ICML:2024,ICLR:2025' into list of tuples."""
    result = []
    for item in value.split(","):
        item = item.strip()
        if ":" in item:
            conf, year = item.split(":", 1)
            result.append((conf.strip(), int(year.strip())))
        else:
            print(f"Warning: ignoring invalid conference format '{item}', expected 'NAME:YEAR'")
    return result


def main():
    parser = argparse.ArgumentParser(
        prog="paper-survey",
        description="Automated academic paper survey pipeline — scrape, analyze, and synthesize "
                    "research papers from top AI conferences using LLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  paper-survey --all                          Run full pipeline (lite mode by default)
  paper-survey --all --full                   Run with PDF download + full analysis
  paper-survey --scrape                       Only scrape papers
  paper-survey --analyze                      Only analyze papers (needs prior scrape)
  paper-survey --survey                       Only generate survey (needs prior analyze)
  paper-survey --from-step analyze            Run from analyze step onward

  Primary config file:
  ./runtime_config.json

  # Temporary runtime overrides:
  paper-survey --all --api-key sk-xxx --model gpt-4o-mini
  paper-survey --all --topic "world models and learned simulators for RL"
  paper-survey --all --topic "retrieval augmented generation and dense retrieval"
  paper-survey --all --arxiv "JEPA self-supervised,latent predictive representation"
  paper-survey --all --arxiv "world model reinforcement learning" --arxiv-from 2025-01-01
  paper-survey --all --conferences "ICML:2024,ICLR:2025"
  paper-survey --all --output-dir ./my_survey_output
        """
    )

    # Pipeline steps
    step_group = parser.add_argument_group("Pipeline steps")
    step_group.add_argument("--all", action="store_true", help="Run full pipeline")
    step_group.add_argument("--scrape", action="store_true", help="Step 1: Scrape papers")
    step_group.add_argument("--download", action="store_true", help="Step 2: Download PDFs")
    step_group.add_argument("--analyze", action="store_true", help="Step 3: Analyze papers")
    step_group.add_argument("--survey", action="store_true", help="Step 4: Generate survey")
    step_group.add_argument("--from-step", type=str, choices=["scrape", "download", "analyze", "survey"],
                            help="Run from a specific step onward")

    # Mode
    mode_group = parser.add_argument_group("Mode")
    mode_ex = mode_group.add_mutually_exclusive_group()
    mode_ex.add_argument("--lite", action="store_true", help="Lite mode: abstract-only, no PDF (default)")
    mode_ex.add_argument("--full", action="store_true", help="Full mode: download PDFs, detailed analysis")

    # Config overrides
    cfg_group = parser.add_argument_group("Configuration overrides")
    cfg_group.add_argument("--api-key", type=str, help="Override llm_api_key from runtime_config.json")
    cfg_group.add_argument("--base-url", type=str, help="LLM API base URL")
    cfg_group.add_argument("--model", type=str, help="LLM model name (e.g., gpt-4o, gpt-4o-mini)")
    cfg_group.add_argument("--openreview-token", type=str, help="Override openreview_api_token from runtime_config.json")
    cfg_group.add_argument("--persona-tag", type=str,
                           help="Override clawcross_persona_tag for ClawCross persona backend (e.g., paper_reporter, ml_reviewer)")
    cfg_group.add_argument("--topic", type=str,
                           help="Research topic for LLM-based paper filtering (natural language, e.g., 'world models and learned simulators')")
    filter_prompt_group = cfg_group.add_mutually_exclusive_group()
    filter_prompt_group.add_argument("--filter-prompt", type=str,
                                     help="Custom paper-filter prompt template passed directly on the command line. Must include {topic} and {papers_text}")
    filter_prompt_group.add_argument("--filter-prompt-file", type=str,
                                     help="Path to a custom paper-filter prompt template. Must include {topic} and {papers_text}")
    cfg_group.add_argument("--conferences", type=str,
                           help="Comma-separated conference:year pairs (e.g., 'ICML:2024,ICLR:2025')")
    cfg_group.add_argument("--max-papers", type=int,
                           help="Hard cap on total candidate papers before LLM filtering; 0 means unlimited")
    cfg_group.add_argument("--arxiv", type=str,
                           help="Comma-separated arXiv search queries (e.g., 'JEPA self-supervised,world model RL')")
    cfg_group.add_argument("--arxiv-max", type=int, default=None,
                           help="Max results per arXiv query (default: 200)")
    cfg_group.add_argument("--arxiv-from", type=str,
                           help="Only include arXiv papers published after this date (e.g., '2025-01-01')")
    cfg_group.add_argument("--output-dir", type=str, help="Output directory (default: ./output)")

    args = parser.parse_args()

    # Apply config overrides
    overrides = {}
    if args.api_key:
        overrides["api_key"] = args.api_key
    if args.base_url:
        overrides["base_url"] = args.base_url
    if args.model:
        overrides["model"] = args.model
    if args.openreview_token:
        overrides["openreview_token"] = args.openreview_token
    if args.persona_tag:
        overrides["clawcross_persona_tag"] = args.persona_tag
    if args.output_dir:
        overrides["output_dir"] = os.path.abspath(args.output_dir)
    if args.topic:
        overrides["topic"] = args.topic
    if args.filter_prompt:
        overrides["filter_prompt_template"] = args.filter_prompt
    if args.filter_prompt_file:
        with open(args.filter_prompt_file, 'r', encoding='utf-8') as f:
            overrides["filter_prompt_template"] = f.read()
    if args.conferences:
        overrides["conferences"] = _parse_conferences(args.conferences)
    if args.max_papers is not None:
        overrides["max_candidate_papers"] = max(0, args.max_papers)
    if args.arxiv:
        overrides["arxiv_queries"] = [q.strip() for q in args.arxiv.split(",") if q.strip()]
    if args.arxiv_max:
        overrides["arxiv_max_results"] = args.arxiv_max
    if args.arxiv_from:
        overrides["arxiv_date_from"] = args.arxiv_from

    # Mode override
    if args.lite:
        overrides["lite_mode"] = True
    elif args.full:
        overrides["lite_mode"] = False

    if overrides:
        config.override(**overrides)

    # Validate required config
    config.validate()

    # Setup logging after config is finalized
    _setup_logging()

    logger = logging.getLogger(__name__)
    logger.info(f"Mode: {'LITE' if config.LITE_MODE else 'FULL'}")
    logger.info(f"Model: {config.LLM_MODEL}")
    logger.info(f"Base URL: {config.LLM_BASE_URL}")
    logger.info(f"Output: {config.OUTPUT_DIR}")
    logger.info(f"Topic: {config.TOPIC[:100]}")
    logger.info(f"Conferences: {config.TARGET_CONFERENCES}")
    logger.info(f"Max candidate papers: {config.MAX_CANDIDATE_PAPERS or 'unlimited'}")
    if config.ARXIV_QUERIES:
        logger.info(f"arXiv queries: {config.ARXIV_QUERIES} (max {config.ARXIV_MAX_RESULTS}/query, from={config.ARXIV_DATE_FROM or 'any'})")
    else:
        logger.info("arXiv: disabled (use --arxiv to enable)")
    if config.OPENREVIEW_API_TOKEN:
        logger.info("OpenReview: authenticated")
    else:
        logger.info("OpenReview: public API (no token)")

    # Default to --all if no step specified
    if not any([args.all, args.scrape, args.download, args.analyze, args.survey, args.from_step]):
        args.all = True

    if args.from_step:
        run_from_step(args.from_step)
    elif args.all:
        run_full_pipeline()
    else:
        papers = None
        if os.path.exists(config.PAPER_LIST_FILE):
            with open(config.PAPER_LIST_FILE, 'r', encoding='utf-8') as f:
                papers = json.load(f)

        if args.scrape:
            papers = step_scrape()
        if args.download:
            papers = step_download(papers)
        if args.analyze:
            papers = step_analyze(papers)
        if args.survey:
            step_survey(papers)


if __name__ == "__main__":
    main()
