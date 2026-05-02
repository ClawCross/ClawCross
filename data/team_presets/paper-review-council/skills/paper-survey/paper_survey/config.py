"""
Configuration for the paper survey pipeline.

Runtime settings are loaded from the project-level JSON config file.
CLI arguments can still override selected settings at runtime.
"""

import json
import os
import sys
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
RUNTIME_CONFIG_FILE = PROJECT_ROOT / "runtime_config.json"


def _load_runtime_config() -> dict:
    """Load project runtime config from JSON file."""
    if not RUNTIME_CONFIG_FILE.exists():
        return {}

    try:
        with RUNTIME_CONFIG_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        print(f"Error: failed to read config file {RUNTIME_CONFIG_FILE}: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict):
        print(f"Error: config file must contain a JSON object: {RUNTIME_CONFIG_FILE}", file=sys.stderr)
        sys.exit(1)

    return data


def _cfg(name: str, default):
    return _RUNTIME_CONFIG.get(name, default)


def _cfg_non_empty(name: str, default):
    value = _RUNTIME_CONFIG.get(name, default)
    return default if value in (None, "") else value


_RUNTIME_CONFIG = _load_runtime_config()


def _infer_clawcross_scope() -> tuple[str, str]:
    """Infer user/team when this package is installed as a ClawCross team skill."""
    parts = PROJECT_ROOT.resolve().parts
    try:
        idx = parts.index("user_files")
        user_id = parts[idx + 1]
    except (ValueError, IndexError):
        user_id = "default"
    try:
        team_idx = parts.index("teams")
        team = parts[team_idx + 1]
    except (ValueError, IndexError):
        team = ""
    return user_id, team


_INFERRED_USER_ID, _INFERRED_TEAM = _infer_clawcross_scope()

# ========================
# ClawCross Persona Backend
# ========================
# Preferred when running inside ClawCross. This calls OASIS send_persona
# instead of a direct OpenAI-compatible API, so no llm_api_key is required.
CLAWCROSS_PERSONA_ENABLED = bool(_cfg("clawcross_persona_enabled", True))
CLAWCROSS_USER_ID = _cfg_non_empty("clawcross_user_id", _INFERRED_USER_ID)
CLAWCROSS_TEAM = _cfg_non_empty("clawcross_team", _INFERRED_TEAM)
CLAWCROSS_PERSONA_TAG = _cfg_non_empty("clawcross_persona_tag", "paper_reporter")
CLAWCROSS_FALLBACK_TO_OPENAI = bool(_cfg("clawcross_fallback_to_openai", True))
CLAWCROSS_PERSONA_TIMEOUT = float(_cfg("clawcross_persona_timeout", 120) or 120)

# ========================
# LLM Configuration
# ========================
# Values are read from runtime_config.json unless overridden by CLI.
LLM_API_KEY = _cfg("llm_api_key", "")
LLM_BASE_URL = _cfg("llm_base_url", "https://api.openai.com/v1")
LLM_MODEL = _cfg("llm_model", "gpt-4o")

# ========================
# OpenReview Configuration
# ========================
# Optional token for higher OpenReview rate limits.
OPENREVIEW_API_TOKEN = _cfg("openreview_api_token", "")

# ========================
# Mode Configuration
# ========================
# LITE_MODE: When True, skip PDF download, only use abstract for analysis,
#            and generate a concise survey focusing on directions and ideas.
LITE_MODE = bool(_cfg("lite_mode", True))

# ========================
# Scraping Configuration
# ========================
# Target conferences and years
# Format: list of (conference, year) tuples
TARGET_CONFERENCES = [
    ("ICML", 2024),
    ("ICLR", 2024),
    ("NeurIPS", 2024),
    ("ICML", 2025),
    ("ICLR", 2025),
    ("NeurIPS", 2025),
    ("ICML", 2026),
    ("ICLR", 2026),
    ("NeurIPS", 2026),
]

# Only include these paper types (oral, spotlight)
TARGET_PAPER_TYPES = ["oral", "spotlight"]

# Research topic for LLM-based paper filtering.
# All scraped papers (title + abstract) are sent to LLM to judge relevance.
# This is a natural language description — be as specific or broad as you like.
TOPIC = _cfg(
    "topic",
    "multi-agent systems, including multi-agent reinforcement learning, LLM-based multi-agent collaboration, emergent communication, cooperative/competitive agents, agent coordination, and agentic AI",
)

FILTER_PROMPT_TEMPLATE = _cfg(
    "filter_prompt_template",
    """You are a research paper filter. Given a research topic and a list of papers (title + abstract), decide which papers are relevant.

TOPIC: {topic}

PAPERS:
{papers_text}

For each paper, output ONLY its index and YES or NO, one per line. Example:
[0] YES
[1] NO
[2] YES

Be inclusive - if the paper is even partially related to the topic, say YES. Only say NO for clearly irrelevant papers.""",
)

# Max concurrent LLM filter calls
MAX_CONCURRENT_FILTER = 20

# Hard cap on total candidate papers before LLM filtering.
# 0 means unlimited.
MAX_CANDIDATE_PAPERS = int(_cfg("max_candidate_papers", 0) or 0)

# ========================
# arXiv Configuration
# ========================
# arXiv search queries. Each query is sent to the arXiv API.
# Leave empty to skip arXiv. Use --arxiv CLI to override.
# Example: ["JEPA self-supervised", "world model reinforcement learning"]
ARXIV_QUERIES = list(_cfg("arxiv_queries", []))
ARXIV_MAX_RESULTS = int(_cfg("arxiv_max_results", 200) or 200)
ARXIV_DATE_FROM = _cfg("arxiv_date_from", "")

# ========================
# Output Configuration
# ========================
# Default output under CWD; overridable via CLI --output-dir.
OUTPUT_DIR = os.path.join(os.getcwd(), "output")
PAPERS_DIR = os.path.join(OUTPUT_DIR, "papers")
PDFS_DIR = os.path.join(OUTPUT_DIR, "pdfs")
REPORTS_DIR = os.path.join(OUTPUT_DIR, "reports")
LOGS_DIR = os.path.join(os.getcwd(), "logs")

# Paper list JSON file
PAPER_LIST_FILE = os.path.join(OUTPUT_DIR, "paper_list.json")
# Final survey report
SURVEY_FILE = os.path.join(OUTPUT_DIR, "survey_report.md")

# ========================
# Download Configuration
# ========================
MAX_CONCURRENT_DOWNLOADS = 5
DOWNLOAD_TIMEOUT = 120  # seconds
REQUEST_DELAY = 2  # seconds between requests to avoid rate limiting

# ========================
# LLM Analysis Configuration
# ========================
MAX_CONCURRENT_ANALYSIS = 20  # Max concurrent LLM analysis threads
ANALYSIS_BATCH_SIZE = 5  # Number of papers to analyze before saving progress

# ========================
# Survey Synthesis Configuration
# ========================
# Keep each synthesis call comfortably below typical context ceilings.
SURVEY_CHUNK_MAX_CHARS = 45000


def _rebuild_paths():
    """Rebuild derived paths after OUTPUT_DIR changes."""
    global PAPERS_DIR, PDFS_DIR, REPORTS_DIR, PAPER_LIST_FILE, SURVEY_FILE
    PAPERS_DIR = os.path.join(OUTPUT_DIR, "papers")
    PDFS_DIR = os.path.join(OUTPUT_DIR, "pdfs")
    REPORTS_DIR = os.path.join(OUTPUT_DIR, "reports")
    PAPER_LIST_FILE = os.path.join(OUTPUT_DIR, "paper_list.json")
    SURVEY_FILE = os.path.join(OUTPUT_DIR, "survey_report.md")


def override(**kwargs):
    """
    Override config values at runtime (called by CLI before pipeline runs).

    Accepted keys: api_key, base_url, model, openreview_token,
                   output_dir, topic, filter_prompt_template,
                   conferences, lite_mode,
                   max_candidate_papers, arxiv_queries,
                   arxiv_max_results, arxiv_date_from,
                   clawcross_persona_enabled, clawcross_user_id,
                   clawcross_team, clawcross_persona_tag,
                   clawcross_fallback_to_openai, clawcross_persona_timeout
    """
    g = globals()

    if kwargs.get("api_key"):
        g["LLM_API_KEY"] = kwargs["api_key"]
    if kwargs.get("base_url"):
        g["LLM_BASE_URL"] = kwargs["base_url"]
    if kwargs.get("model"):
        g["LLM_MODEL"] = kwargs["model"]
    if kwargs.get("openreview_token"):
        g["OPENREVIEW_API_TOKEN"] = kwargs["openreview_token"]
    if kwargs.get("lite_mode") is not None:
        g["LITE_MODE"] = kwargs["lite_mode"]
    if kwargs.get("output_dir"):
        g["OUTPUT_DIR"] = kwargs["output_dir"]
        g["LOGS_DIR"] = os.path.join(kwargs["output_dir"], "..", "logs")
        _rebuild_paths()
    if kwargs.get("topic"):
        g["TOPIC"] = kwargs["topic"]
    if kwargs.get("filter_prompt_template"):
        g["FILTER_PROMPT_TEMPLATE"] = kwargs["filter_prompt_template"]
    if "conferences" in kwargs:
        g["TARGET_CONFERENCES"] = kwargs["conferences"]
    if kwargs.get("max_candidate_papers") is not None:
        g["MAX_CANDIDATE_PAPERS"] = kwargs["max_candidate_papers"]
    if "arxiv_queries" in kwargs:
        g["ARXIV_QUERIES"] = kwargs["arxiv_queries"]
    if "arxiv_max_results" in kwargs:
        g["ARXIV_MAX_RESULTS"] = kwargs["arxiv_max_results"]
    if "arxiv_date_from" in kwargs:
        g["ARXIV_DATE_FROM"] = kwargs["arxiv_date_from"]
    if kwargs.get("clawcross_persona_enabled") is not None:
        g["CLAWCROSS_PERSONA_ENABLED"] = bool(kwargs["clawcross_persona_enabled"])
    if kwargs.get("clawcross_user_id"):
        g["CLAWCROSS_USER_ID"] = kwargs["clawcross_user_id"]
    if kwargs.get("clawcross_team") is not None:
        g["CLAWCROSS_TEAM"] = kwargs["clawcross_team"]
    if kwargs.get("clawcross_persona_tag"):
        g["CLAWCROSS_PERSONA_TAG"] = kwargs["clawcross_persona_tag"]
    if kwargs.get("clawcross_fallback_to_openai") is not None:
        g["CLAWCROSS_FALLBACK_TO_OPENAI"] = bool(kwargs["clawcross_fallback_to_openai"])
    if kwargs.get("clawcross_persona_timeout") is not None:
        g["CLAWCROSS_PERSONA_TIMEOUT"] = float(kwargs["clawcross_persona_timeout"])


def validate():
    """Validate that required config values are present. Call before pipeline run."""
    if CLAWCROSS_PERSONA_ENABLED:
        return
    if not LLM_API_KEY:
        print(
            "Error: LLM API key is required.\n"
            f"Set `llm_api_key` in {RUNTIME_CONFIG_FILE} or pass --api-key.\n"
            "Alternatively set `clawcross_persona_enabled` to true and run inside ClawCross.\n"
            "\n"
            f"  edit {RUNTIME_CONFIG_FILE}\n"
            "  paper-survey --all\n",
            file=sys.stderr,
        )
        sys.exit(1)
