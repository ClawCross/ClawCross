"""
Step 4: Generate a comprehensive survey report from all individual paper reports.
Uses LLM to synthesize findings across all analyzed papers.

No content truncation — full reports are passed to LLM for synthesis.
"""

import json
import os
import logging
from typing import List, Dict
from datetime import datetime

from paper_survey.config import (
    PAPER_LIST_FILE, REPORTS_DIR, SURVEY_FILE, OUTPUT_DIR,
    LITE_MODE, SURVEY_CHUNK_MAX_CHARS,
)
from paper_survey.llm import send_to_llm

logger = logging.getLogger(__name__)


def _build_chunked_contexts(reports: List[Dict], max_chars: int) -> List[str]:
    """Split report context into multiple chunks to stay within model limits."""
    chunks = []
    current_parts = []
    current_len = 0

    for report in reports:
        summary = f"### {report['title']} ({report['conference']} {report['year']}, {report['paper_type']})\n"
        if report['report']:
            summary += report['report']
        elif report['abstract']:
            summary += f"Abstract: {report['abstract']}"

        separator_len = 6 if current_parts else 0
        if current_parts and current_len + separator_len + len(summary) > max_chars:
            chunks.append("\n\n---\n\n".join(current_parts))
            current_parts = [summary]
            current_len = len(summary)
            continue

        current_parts.append(summary)
        current_len += separator_len + len(summary)

    if current_parts:
        chunks.append("\n\n---\n\n".join(current_parts))

    return chunks


def _call_survey_llm(context: str, task: str) -> str:
    """Call the survey LLM with shared instructions."""
    return send_to_llm(
        messages=[
            {
                "role": "system",
                "content": "You are a senior AI researcher writing a survey paper on Multi-Agent Systems. "
                           "Write in academic style, be comprehensive yet concise. Use Markdown formatting. "
                           "Cite papers by their titles when referencing them."
            },
            {
                "role": "user",
                "content": f"""Based on the following paper reports from top AI conferences (ICML, ICLR, NeurIPS),
please write the requested section of the survey.

**Papers Context:**
{context}

---

**Task:** {task}
"""
            },
        ],
        tag="survey_generation",
        temperature=0.4,
    )


def load_all_reports(papers: List[Dict]) -> List[Dict]:
    """Load all individual paper reports."""
    reports = []
    for paper in papers:
        report_path = paper.get("report_path", "")
        report_text = ""
        if report_path and os.path.exists(report_path):
            with open(report_path, 'r', encoding='utf-8') as f:
                report_text = f.read()

        reports.append({
            "title": paper.get("title", "Unknown"),
            "conference": paper.get("conference", "Unknown"),
            "year": paper.get("year", "Unknown"),
            "paper_type": paper.get("paper_type", "Unknown"),
            "url": paper.get("url", ""),
            "report": report_text,
            "abstract": paper.get("abstract", ""),
        })
    return reports


def generate_papers_summary(reports: List[Dict]) -> str:
    """Generate a brief summary table of all papers for the survey."""
    papers_info = []
    for i, r in enumerate(reports, 1):
        papers_info.append(
            f"{i}. **{r['title']}** - {r['conference']} {r['year']} ({r['paper_type']})\n"
            f"   URL: {r['url']}"
        )
    return "\n".join(papers_info)


def generate_survey_section(reports: List[Dict], section_prompt: str) -> str:
    """Generate a section of the survey using chunked LLM synthesis."""

    contexts = _build_chunked_contexts(reports, SURVEY_CHUNK_MAX_CHARS)

    try:
        partial_sections = []
        total_chunks = len(contexts)

        for index, context in enumerate(contexts, 1):
            logger.info(f"Generating survey section chunk {index}/{total_chunks}...")
            chunk_task = (
                f"{section_prompt}\n\n"
                f"This is chunk {index} of {total_chunks}. Cover only the papers in this chunk, "
                "while preserving concrete paper-to-theme mappings and specific findings."
            )
            partial_sections.append(_call_survey_llm(context, chunk_task))

        if len(partial_sections) == 1:
            return partial_sections[0]

        combined_context = "\n\n---\n\n".join(
            f"### Partial Section {idx}\n{section}"
            for idx, section in enumerate(partial_sections, 1)
        )
        merge_task = (
            f"{section_prompt}\n\n"
            "Merge the partial sections into one coherent final section. "
            "Deduplicate repeated themes, keep concrete paper references, and preserve broad coverage."
        )
        return _call_survey_llm(combined_context, merge_task)
    except Exception as e:
        logger.error(f"LLM survey generation failed: {e}")
        return f"*Section generation failed: {str(e)}*"


def generate_survey(papers: List[Dict] = None):
    """Generate the complete survey report."""
    if papers is None:
        if not os.path.exists(PAPER_LIST_FILE):
            logger.error(f"Paper list not found: {PAPER_LIST_FILE}")
            return
        with open(PAPER_LIST_FILE, 'r', encoding='utf-8') as f:
            papers = json.load(f)

    logger.info(f"Generating survey from {len(papers)} papers...")

    reports = load_all_reports(papers)

    survey_sections = []

    # Title and metadata
    date_str = datetime.now().strftime("%Y-%m-%d")
    mode_label = " (Lite Mode)" if LITE_MODE else ""
    header = f"""# Survey: Multi-Agent Systems in Top AI Conferences{mode_label}
## (ICML, ICLR, NeurIPS - Oral & Spotlight Papers)

**Generated:** {date_str}
**Total Papers Analyzed:** {len(reports)}
**Mode:** {"Lite (abstract-only)" if LITE_MODE else "Full (PDF-based)"}

---

"""
    survey_sections.append(header)

    # Paper list (always included)
    logger.info("Generating paper list...")
    papers_list = generate_papers_summary(reports)
    survey_sections.append(f"## Paper List\n\n{papers_list}\n\n---\n\n")

    if LITE_MODE:
        # === LITE MODE: 3 concise sections ===

        logger.info("[Lite] Generating research landscape overview...")
        overview = generate_survey_section(
            reports,
            "Write a concise **Research Landscape Overview** (300-500 words). "
            "For EACH paper, clearly identify: (1) the specific RESEARCH PROBLEM it tackles, "
            "(2) the broader RESEARCH DIRECTION it belongs to. "
            "Then group papers by research direction/theme (e.g., cooperative MARL, LLM-based multi-agent, "
            "emergent communication, decentralized planning, scalable coordination, etc.). "
            "For each direction, list which papers belong to it, what problem each paper solves, "
            "and summarize the general approach in 1-2 sentences. "
            "Focus on the BIG PICTURE — what directions are hot and why."
        )
        survey_sections.append(f"## Research Directions Overview\n\n{overview}\n\n---\n\n")

        logger.info("[Lite] Generating key ideas & trends...")
        ideas = generate_survey_section(
            reports,
            "Write a concise **Key Ideas & Trends** section (300-400 words). "
            "Identify: (1) the most novel/interesting research PROBLEMS being addressed across these papers, "
            "(2) emerging RESEARCH DIRECTIONS and trends in multi-agent systems, "
            "(3) what distinguishes oral/spotlight papers — what research problems and approaches make them stand out? "
            "For each trend, reference which papers exemplify it and what problem they solve. "
            "Keep it brief and insightful."
        )
        survey_sections.append(f"## Key Ideas & Trends\n\n{ideas}\n\n---\n\n")

        # Per-paper summary table
        logger.info("[Lite] Generating per-paper summary table...")
        per_paper_section = "## Per-Paper Quick Summary\n\n"
        per_paper_section += "| # | Title | Venue | Type | Research Problem | Direction | Core Idea |\n"
        per_paper_section += "|---|-------|-------|------|-----------------|-----------|-----------|\n"
        for i, r in enumerate(reports, 1):
            title_short = r['title'][:60] + ('...' if len(r['title']) > 60 else '')
            core_idea = ""
            if r['report']:
                lines = r['report'].split('\n')
                for j, line in enumerate(lines):
                    if 'TL;DR' in line or 'Core Direction' in line or 'Key Idea' in line:
                        for k in range(j+1, min(j+3, len(lines))):
                            if lines[k].strip() and not lines[k].startswith('#'):
                                core_idea = lines[k].strip()[:80]
                                break
                        break
            if not core_idea:
                core_idea = (r.get('abstract', '') or '')[:80]
            per_paper_section += f"| {i} | [{title_short}]({r['url']}) | {r['conference']} {r['year']} | {r['paper_type']} | - | - | {core_idea} |\n"
        per_paper_section += "\n"
        survey_sections.append(per_paper_section)

    else:
        # === FULL MODE: 7 sections ===

        logger.info("Generating executive summary...")
        exec_summary = generate_survey_section(
            reports,
            "Write an **Executive Summary** (400-600 words) that provides a high-level overview of "
            "the multi-agent systems research represented by these papers. Cover: "
            "(1) the overall landscape and trends, "
            "(2) key research PROBLEMS being addressed and DIRECTIONS being pursued, "
            "(3) the most impactful contributions. "
            "For each major theme, clearly state what PROBLEM the papers in that theme are trying to solve."
        )
        survey_sections.append(f"## Executive Summary\n\n{exec_summary}\n\n---\n\n")

        logger.info("Generating taxonomy...")
        taxonomy = generate_survey_section(
            reports,
            "Create a **Taxonomy and Categorization** of the papers. Group them by research PROBLEM and DIRECTION "
            "(e.g., multi-agent reinforcement learning, LLM-based multi-agent systems, emergent communication, "
            "cooperative/competitive settings, multi-agent planning, etc.). For each category: "
            "(1) clearly state the core RESEARCH PROBLEM this direction addresses, "
            "(2) list the papers that belong to it, "
            "(3) for each paper, briefly state the specific problem it solves and its approach."
        )
        survey_sections.append(f"## Taxonomy and Categorization\n\n{taxonomy}\n\n---\n\n")

        logger.info("Generating methodology overview...")
        methods = generate_survey_section(
            reports,
            "Write a **Key Methods and Approaches** section that synthesizes the main methodological "
            "contributions across all papers. Identify: "
            "(1) common algorithmic frameworks, "
            "(2) novel techniques introduced, "
            "(3) theoretical contributions, "
            "(4) engineering innovations."
        )
        survey_sections.append(f"## Key Methods and Approaches\n\n{methods}\n\n---\n\n")

        logger.info("Generating trends analysis...")
        trends = generate_survey_section(
            reports,
            "Write a **Trends and Insights** section that analyzes: "
            "(1) emerging trends in multi-agent systems research, "
            "(2) shifts from previous years (if identifiable), "
            "(3) the growing role of LLMs in multi-agent systems, "
            "(4) connections between different research threads, "
            "(5) gaps in current research."
        )
        survey_sections.append(f"## Trends and Insights\n\n{trends}\n\n---\n\n")

        logger.info("Generating benchmarks overview...")
        benchmarks = generate_survey_section(
            reports,
            "Write a **Benchmarks and Evaluation** section that covers: "
            "(1) what benchmarks and evaluation metrics are commonly used, "
            "(2) what environments/domains are studied, "
            "(3) standardization efforts in multi-agent evaluation."
        )
        survey_sections.append(f"## Benchmarks and Evaluation\n\n{benchmarks}\n\n---\n\n")

        logger.info("Generating future directions...")
        future = generate_survey_section(
            reports,
            "Write a **Future Directions** section that proposes: "
            "(1) open problems identified across papers, "
            "(2) promising research directions, "
            "(3) potential applications and impact, "
            "(4) challenges that need to be addressed. "
            "Be specific and reference papers where relevant."
        )
        survey_sections.append(f"## Future Directions\n\n{future}\n\n---\n\n")

        # Per-paper summaries (full abstract, no truncation)
        logger.info("Generating individual summaries...")
        per_paper_section = "## Individual Paper Summaries\n\n"
        for r in reports:
            per_paper_section += f"### {r['title']}\n"
            per_paper_section += f"- **Conference:** {r['conference']} {r['year']} ({r['paper_type']})\n"
            per_paper_section += f"- **URL:** {r['url']}\n"
            if r['abstract']:
                per_paper_section += f"- **Abstract:** {r['abstract']}\n"
            per_paper_section += "\n"
        survey_sections.append(per_paper_section)

    # Combine all sections
    full_survey = "\n".join(survey_sections)

    # Save survey
    os.makedirs(os.path.dirname(SURVEY_FILE), exist_ok=True)
    with open(SURVEY_FILE, 'w', encoding='utf-8') as f:
        f.write(full_survey)

    print(f"\n{'='*60}")
    print(f"SURVEY GENERATION COMPLETE")
    print(f"{'='*60}")
    print(f"Survey saved to: {SURVEY_FILE}")
    print(f"Total papers covered: {len(reports)}")

    return full_survey
