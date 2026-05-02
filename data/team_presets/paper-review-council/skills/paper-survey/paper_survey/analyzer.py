"""
Step 3: Use LLM to analyze each paper and generate reports.
Extracts text from PDFs and sends to LLM for analysis.

No content truncation — full paper text and full LLM output are preserved.
"""

import json
import os
import re
import logging
import threading
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from paper_survey.config import (
    PAPER_LIST_FILE, REPORTS_DIR,
    ANALYSIS_BATCH_SIZE, MAX_CONCURRENT_ANALYSIS, LITE_MODE,
)
from paper_survey.llm import send_to_llm

logger = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from a PDF file using available libraries. Reads ALL pages."""
    text = ""

    # Try pdfplumber first (better extraction quality)
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"
        if text.strip():
            return text.strip()
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"pdfplumber failed for {pdf_path}: {e}")

    # Try PyPDF2
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n\n"
        if text.strip():
            return text.strip()
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"PyPDF2 failed for {pdf_path}: {e}")

    logger.warning(f"Could not extract text from {pdf_path}")
    return ""


def analyze_paper_with_llm(
    paper: Dict,
    paper_text: str,
) -> str:
    """Use LLM to analyze a single paper and generate a full report."""

    system_prompt = """You are an expert AI researcher specializing in Multi-Agent Systems.
Your task is to analyze academic papers and provide comprehensive, structured reports.
Be thorough but concise. Focus on key contributions, methods, and relevance to the multi-agent field.
Output your report in Markdown format."""

    user_prompt = f"""Please analyze the following paper and generate a detailed report.

**Paper Information:**
- Title: {paper.get('title', 'Unknown')}
- Conference: {paper.get('conference', 'Unknown')} {paper.get('year', '')}
- Paper Type: {paper.get('paper_type', 'Unknown')}
- URL: {paper.get('url', '')}

**Paper Content:**
{paper_text}

---

Please generate a report with the following structure:

## 1. Basic Information
- Title, authors (if identifiable), conference, paper type

## 2. Abstract / TL;DR
- A 3-5 sentence summary of the paper

## 3. Research Problem & Direction (IMPORTANT — be specific and detailed)
- What specific PROBLEM does this paper aim to solve? State it clearly and precisely.
- What broader RESEARCH DIRECTION does it belong to? (e.g., cooperative MARL, LLM-based multi-agent orchestration, emergent communication, decentralized planning, scalable agent coordination, etc.)
- Why is this problem important for multi-agent systems?

## 4. Key Contributions
- List the main contributions (3-5 bullet points)

## 5. Methodology
- Describe the proposed approach/method
- Key technical details

## 6. Experiments & Results
- What experiments were conducted?
- Key findings and metrics

## 7. Relevance to Multi-Agent Systems
- How does this paper relate to the multi-agent field?
- What aspects of multi-agent systems does it advance?

## 8. Strengths & Limitations
- Main strengths of the paper
- Potential limitations or areas for improvement

## 9. Key Takeaways
- 3-5 most important takeaways for researchers in multi-agent systems
"""

    try:
        return send_to_llm(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tag="paper_analysis_full",
            temperature=0.3,
        )
    except Exception as e:
        logger.error(f"LLM analysis failed for '{paper.get('title', 'Unknown')}': {e}")
        return f"**Analysis failed:** {str(e)}"


def analyze_paper_lite(paper: Dict) -> str:
    """Lite mode: concise analysis based on abstract only, focusing on direction and ideas."""

    system_prompt = """You are an expert AI researcher. Given a paper's title and abstract,
provide a brief and focused analysis. Be concise — focus on the core direction, key idea, and relevance to multi-agent systems."""

    user_prompt = f"""Briefly analyze this paper:

**Title:** {paper.get('title', 'Unknown')}
**Conference:** {paper.get('conference', 'Unknown')} {paper.get('year', '')} ({paper.get('paper_type', '')})
**Abstract:** {paper.get('abstract', 'Not available')}

Provide a SHORT report (each section 1-3 sentences max):
## 1. TL;DR (1-2 sentences)
## 2. Research Problem & Direction
- What specific PROBLEM does this paper aim to solve? (e.g., credit assignment in MARL, scalability of agent communication, emergent coordination, etc.)
- What broader RESEARCH DIRECTION does it belong to? (e.g., cooperative MARL, LLM-based multi-agent, emergent communication, decentralized planning, etc.)
## 3. Core Approach & Novelty
- What is the proposed method/framework?
- What makes it novel compared to prior work?
## 4. Relevance to Multi-Agent Systems
## 5. Potential Impact
"""

    try:
        return send_to_llm(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tag="paper_analysis_lite",
            temperature=0.3,
        )
    except Exception as e:
        logger.error(f"LLM lite analysis failed for '{paper.get('title', 'Unknown')}': {e}")
        return f"**Analysis failed:** {str(e)}"


def analyze_paper_without_pdf(paper: Dict) -> str:
    """Analyze a paper using only its title and abstract (when PDF is unavailable)."""

    system_prompt = """You are an expert AI researcher specializing in Multi-Agent Systems.
Analyze the given paper based on its title and abstract. Provide as much insight as possible."""

    user_prompt = f"""Based on the following paper information, provide an analysis:

**Title:** {paper.get('title', 'Unknown')}
**Conference:** {paper.get('conference', 'Unknown')} {paper.get('year', '')}
**Paper Type:** {paper.get('paper_type', 'Unknown')}
**Abstract:** {paper.get('abstract', 'Not available')}
**URL:** {paper.get('url', '')}

Please provide:
## 1. Basic Information
## 2. Abstract / TL;DR
## 3. Research Problem & Direction (IMPORTANT — be specific)
- What specific PROBLEM does this paper aim to solve?
- What broader RESEARCH DIRECTION does it belong to? (e.g., cooperative MARL, LLM-based agents, emergent communication, decentralized planning, etc.)
- Why is this problem important?
## 4. Expected Contributions
## 5. Relevance to Multi-Agent Systems
## 6. Key Takeaways

Note: This analysis is based on title and abstract only. Full paper was not available.
"""

    try:
        return send_to_llm(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tag="paper_analysis_no_pdf",
            temperature=0.3,
        )
    except Exception as e:
        logger.error(f"LLM analysis (no PDF) failed for '{paper.get('title', 'Unknown')}': {e}")
        return f"**Analysis failed:** {str(e)}"


def save_report(paper: Dict, report: str, report_dir: str = None):
    """Save individual paper report to file."""
    if report_dir is None:
        report_dir = REPORTS_DIR

    conf = paper.get("conference", "unknown")
    year = paper.get("year", "unknown")
    conf_dir = os.path.join(report_dir, f"{conf}_{year}")
    os.makedirs(conf_dir, exist_ok=True)

    # Create filename from title (cosmetic truncation for filesystem only)
    title = paper.get("title", "unknown")
    clean_title = re.sub(r'[^\w\s-]', '', title)
    clean_title = re.sub(r'\s+', '_', clean_title.strip())[:80]
    filepath = os.path.join(conf_dir, f"{clean_title}.md")

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"# Paper Report: {title}\n\n")
        f.write(f"- **Conference:** {conf} {year}\n")
        f.write(f"- **Paper Type:** {paper.get('paper_type', 'unknown')}\n")
        f.write(f"- **URL:** {paper.get('url', '')}\n")
        f.write(f"- **PDF:** {paper.get('pdf_url', '')}\n\n")
        f.write("---\n\n")
        f.write(report)
        f.write("\n")

    paper["report_path"] = filepath
    return filepath


def _analyze_single_paper(
    paper: Dict,
    index: int,
    total: int,
) -> Dict:
    """Analyze a single paper (thread-safe). Returns updated paper dict."""
    title_short = paper.get('title', 'Unknown')[:60]

    # Check if report already exists
    existing_report = paper.get("report_path", "")
    if existing_report and os.path.exists(existing_report):
        logger.info(f"[{index+1}/{total}] Report already exists, skipping: {title_short}...")
        return paper

    logger.info(f"[{index+1}/{total}] Analyzing: {title_short}...")

    if LITE_MODE:
        report = analyze_paper_lite(paper)
    else:
        pdf_path = paper.get("pdf_local_path", "")

        if pdf_path and os.path.exists(pdf_path):
            paper_text = extract_text_from_pdf(pdf_path)
            if paper_text:
                report = analyze_paper_with_llm(paper, paper_text)
            else:
                logger.warning(f"[{index+1}/{total}] Could not extract text from PDF, using abstract only")
                report = analyze_paper_without_pdf(paper)
        else:
            logger.info(f"[{index+1}/{total}] No PDF available, using title/abstract only")
            report = analyze_paper_without_pdf(paper)

    # Save individual report
    report_path = save_report(paper, report)
    paper["report_path"] = report_path
    paper["analyzed"] = True

    logger.info(f"[{index+1}/{total}] Done: {title_short}...")
    return paper


def run_analysis(papers: List[Dict] = None):
    """Main entry point for paper analysis. Uses multithreading for parallel LLM calls."""
    if papers is None:
        if not os.path.exists(PAPER_LIST_FILE):
            logger.error(f"Paper list not found: {PAPER_LIST_FILE}. Run scraper first.")
            return []
        with open(PAPER_LIST_FILE, 'r', encoding='utf-8') as f:
            papers = json.load(f)

    total = len(papers)
    logger.info(f"Analyzing {total} papers with LLM (max {MAX_CONCURRENT_ANALYSIS} threads)...")

    os.makedirs(REPORTS_DIR, exist_ok=True)

    # Thread-safe container for results
    results_lock = threading.Lock()
    results_dict = {}
    save_counter = [0]

    def on_future_done(future, idx):
        try:
            result = future.result()
        except Exception as e:
            logger.error(f"Analysis thread error for paper index {idx}: {e}")
            result = papers[idx]
            result["analyzed"] = False

        with results_lock:
            results_dict[idx] = result
            save_counter[0] += 1
            if save_counter[0] % ANALYSIS_BATCH_SIZE == 0:
                _save_progress(results_dict, papers, total)

    def _save_progress(rdict, original_papers, total_count):
        merged = []
        for i in range(total_count):
            if i in rdict:
                merged.append(rdict[i])
            else:
                merged.append(original_papers[i])
        try:
            with open(PAPER_LIST_FILE, 'w', encoding='utf-8') as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
            logger.info(f"Progress saved ({len(rdict)}/{total_count})")
        except Exception as e:
            logger.error(f"Failed to save progress: {e}")

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_ANALYSIS) as executor:
        futures = {}
        for i, paper in enumerate(papers):
            future = executor.submit(
                _analyze_single_paper, paper, i, total
            )
            future.add_done_callback(lambda f, idx=i: on_future_done(f, idx))
            futures[future] = i

        with tqdm(total=total, desc="Analyzing papers") as pbar:
            for future in as_completed(futures):
                pbar.update(1)

    # Build final ordered list
    analyzed_papers = []
    for i in range(total):
        analyzed_papers.append(results_dict.get(i, papers[i]))

    # Final save
    with open(PAPER_LIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(analyzed_papers, f, indent=2, ensure_ascii=False)

    analyzed_count = sum(1 for p in analyzed_papers if p.get("analyzed"))
    print(f"\n{'='*60}")
    print(f"PAPER ANALYSIS COMPLETE")
    print(f"{'='*60}")
    print(f"Analyzed: {analyzed_count}/{len(analyzed_papers)}")
    print(f"Reports saved to: {REPORTS_DIR}")

    return analyzed_papers
