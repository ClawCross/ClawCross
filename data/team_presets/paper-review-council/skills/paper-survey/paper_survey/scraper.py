"""
Step 1: Scrape paper lists from ICML, ICLR, NeurIPS (Oral & Spotlight).
Filter for related papers by configurable keywords.

Data sources:
- OpenReview API for ICLR/NeurIPS/ICML (primary source, structured data)
- Conference proceeding pages as fallback
"""

import json
import os
import re
import time
import logging
from typing import List, Dict, Optional
from urllib.parse import urljoin
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from paper_survey.config import (
    TARGET_CONFERENCES, TARGET_PAPER_TYPES, TOPIC, FILTER_PROMPT_TEMPLATE,
    PAPER_LIST_FILE, OUTPUT_DIR, REQUEST_DELAY, OPENREVIEW_API_TOKEN,
    MAX_CONCURRENT_FILTER, MAX_CANDIDATE_PAPERS,
    ARXIV_QUERIES, ARXIV_MAX_RESULTS, ARXIV_DATE_FROM,
)
from paper_survey.llm import send_to_llm

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _openreview_headers() -> dict:
    """Build request headers for OpenReview API, with optional auth token."""
    headers = dict(HEADERS)
    if OPENREVIEW_API_TOKEN:
        headers["Authorization"] = f"Bearer {OPENREVIEW_API_TOKEN}"
    return headers


# ========================
# LLM-based Paper Filtering
# ========================

def _build_filter_prompt(topic: str, papers_text: str, prompt_template: str) -> str:
    try:
        return prompt_template.format(topic=topic, papers_text=papers_text)
    except KeyError as exc:
        raise ValueError(
            f"filter_prompt_template is missing placeholder: {exc}. "
            "Required placeholders: {topic}, {papers_text}"
        ) from exc


def _llm_filter_batch(papers: List[Dict], topic: str, prompt_template: str) -> List[bool]:
    """
    Use LLM to judge relevance of a batch of papers to the given topic.
    Sends all papers in one call, returns list of True/False.
    """
    paper_entries = []
    for i, p in enumerate(papers):
        title = p.get("title", "")
        abstract = p.get("abstract", "") or ""
        paper_entries.append(f"[{i}] Title: {title}\nAbstract: {abstract}")

    papers_text = "\n\n".join(paper_entries)

    prompt = _build_filter_prompt(topic, papers_text, prompt_template)

    try:
        text = send_to_llm(
            messages=[{"role": "user", "content": prompt}],
            tag="paper_filter",
            temperature=0,
        )

        # Parse results
        results = [False] * len(papers)
        for line in text.strip().split("\n"):
            line = line.strip()
            match = re.match(r'\[(\d+)\]\s*(YES|NO)', line, re.IGNORECASE)
            if match:
                idx = int(match.group(1))
                if 0 <= idx < len(papers):
                    results[idx] = match.group(2).upper() == "YES"
        return results

    except Exception as e:
        logger.error(f"LLM filter call failed: {e}")
        # On failure, include all papers (fail-open)
        return [True] * len(papers)


def filter_papers_with_llm(
    papers: List[Dict],
    topic: str,
    prompt_template: str = FILTER_PROMPT_TEMPLATE,
) -> List[Dict]:
    """
    Filter papers by relevance to topic using LLM.
    Papers are batched (50 per call) and processed concurrently.
    """
    if not papers:
        return []

    # Batch papers (50 per LLM call to stay within context limits)
    batch_size = 50
    batches = [papers[i:i+batch_size] for i in range(0, len(papers), batch_size)]

    logger.info(f"LLM filtering {len(papers)} papers in {len(batches)} batches (topic: {topic[:80]}...)")

    all_results = [None] * len(papers)

    def process_batch(batch_idx):
        batch = batches[batch_idx]
        return batch_idx, _llm_filter_batch(batch, topic, prompt_template)

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_FILTER) as executor:
        futures = {executor.submit(process_batch, i): i for i in range(len(batches))}
        with tqdm(total=len(batches), desc="LLM filtering papers") as pbar:
            for future in as_completed(futures):
                batch_idx, batch_results = future.result()
                start = batch_idx * batch_size
                for j, relevant in enumerate(batch_results):
                    all_results[start + j] = relevant
                pbar.update(1)

    filtered = [p for p, relevant in zip(papers, all_results) if relevant]
    return filtered


# ========================
# OpenReview API Scraping
# ========================

def get_openreview_venue_id(conference: str, year: int) -> Optional[str]:
    """Get the OpenReview venue ID for a given conference and year."""
    venue_map = {
        ("ICLR", 2024): "ICLR.cc/2024/Conference",
        ("ICLR", 2025): "ICLR.cc/2025/Conference",
        ("ICLR", 2026): "ICLR.cc/2026/Conference",
        ("NeurIPS", 2024): "NeurIPS.cc/2024/Conference",
        ("NeurIPS", 2025): "NeurIPS.cc/2025/Conference",
        ("NeurIPS", 2026): "NeurIPS.cc/2026/Conference",
        ("ICML", 2024): "ICML.cc/2024/Conference",
        ("ICML", 2025): "ICML.cc/2025/Conference",
        ("ICML", 2026): "ICML.cc/2026/Conference",
    }
    return venue_map.get((conference, year))


def scrape_openreview_papers(conference: str, year: int, paper_type: str) -> List[Dict]:
    """
    Scrape papers from OpenReview API.
    paper_type: 'oral' or 'spotlight'
    """
    venue_id = get_openreview_venue_id(conference, year)
    if not venue_id:
        logger.warning(f"No OpenReview venue ID for {conference} {year}")
        return []

    papers = []
    base_url = "https://api2.openreview.net/notes"
    or_headers = _openreview_headers()

    # Different venues use different invitation formats
    invitation_patterns = []
    if paper_type == "oral":
        invitation_patterns = [
            f"{venue_id}/-/Oral",
            f"{venue_id}/-/oral",
        ]
    elif paper_type == "spotlight":
        invitation_patterns = [
            f"{venue_id}/-/Spotlight",
            f"{venue_id}/-/spotlight",
        ]

    # Build multiple search parameter sets for robustness
    search_params_list = []

    # Method 1: Search by invitation
    for inv in invitation_patterns:
        search_params_list.append({
            "invitation": inv,
            "details": "original",
            "limit": 1000,
            "offset": 0,
        })

    # Method 2: Search via content.venue containing oral/spotlight
    venue_search_terms = {
        "oral": ["Oral", "oral"],
        "spotlight": ["Spotlight", "spotlight"],
    }
    for term in venue_search_terms.get(paper_type, []):
        search_params_list.append({
            "content.venue": f"{conference} {year} {term}",
            "details": "original",
            "limit": 1000,
            "offset": 0,
        })
        search_params_list.append({
            "content.venueid": f"{venue_id}/{term}",
            "details": "original",
            "limit": 1000,
            "offset": 0,
        })

    seen_ids = set()
    for params in search_params_list:
        try:
            logger.info(f"Querying OpenReview: {params}")
            resp = requests.get(base_url, params=params, headers=or_headers, timeout=30)
            if resp.status_code != 200:
                logger.debug(f"OpenReview returned {resp.status_code} for params {params}")
                continue

            data = resp.json()
            notes = data.get("notes", [])
            logger.info(f"Found {len(notes)} notes for params: {params}")

            for note in notes:
                note_id = note.get("id", "")
                if note_id in seen_ids:
                    continue
                seen_ids.add(note_id)

                content = note.get("content", {})

                # Handle different content formats (v1 vs v2 API)
                title = content.get("title", {})
                if isinstance(title, dict):
                    title = title.get("value", "")

                abstract = content.get("abstract", {})
                if isinstance(abstract, dict):
                    abstract = abstract.get("value", "")

                # Get PDF link
                pdf_link = content.get("pdf", {})
                if isinstance(pdf_link, dict):
                    pdf_link = pdf_link.get("value", "")
                if pdf_link and not pdf_link.startswith("http"):
                    pdf_link = f"https://openreview.net{pdf_link}"

                paper_url = f"https://openreview.net/forum?id={note_id}"

                paper = {
                    "title": title,
                    "abstract": abstract,
                    "conference": conference,
                    "year": year,
                    "paper_type": paper_type,
                    "url": paper_url,
                    "pdf_url": pdf_link if pdf_link else f"https://openreview.net/pdf?id={note_id}",
                    "openreview_id": note_id,
                    "source": "openreview",
                }
                papers.append(paper)

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            logger.error(f"Error querying OpenReview with params {params}: {e}")
            continue

    logger.info(f"Total unique papers from OpenReview for {conference} {year} {paper_type}: {len(papers)}")
    return papers


# ========================
# Fallback: Conference Website Scraping
# ========================

def scrape_icml_proceedings(year: int) -> List[Dict]:
    """Scrape ICML papers from proceedings.mlr.press as fallback."""
    volume_map = {
        2024: "v235",
        2025: "v250",
    }
    volume = volume_map.get(year)
    if not volume:
        logger.warning(f"No ICML proceedings volume for year {year}")
        return []

    url = f"https://proceedings.mlr.press/{volume}/"
    papers = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            logger.warning(f"Cannot access ICML proceedings: {url}")
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        paper_entries = soup.find_all('div', class_='paper')

        for entry in paper_entries:
            title_tag = entry.find('p', class_='title')
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)

            links = entry.find_all('a')
            paper_url = ""
            pdf_url = ""
            for link in links:
                href = link.get('href', '')
                text = link.get_text(strip=True).lower()
                if 'abs' in text or 'abstract' in text:
                    paper_url = href if href.startswith('http') else urljoin(url, href)
                if 'pdf' in text or href.endswith('.pdf'):
                    pdf_url = href if href.startswith('http') else urljoin(url, href)

            if not paper_url and links:
                paper_url = links[0].get('href', '')
                if not paper_url.startswith('http'):
                    paper_url = urljoin(url, paper_url)

            paper = {
                "title": title,
                "abstract": "",
                "conference": "ICML",
                "year": year,
                "paper_type": "unknown",
                "url": paper_url,
                "pdf_url": pdf_url,
                "source": "proceedings",
            }
            papers.append(paper)

    except Exception as e:
        logger.error(f"Error scraping ICML proceedings: {e}")

    return papers


def scrape_neurips_papers(year: int) -> List[Dict]:
    """Scrape NeurIPS papers from neurips.cc as fallback."""
    papers = []
    for paper_type in ["oral", "spotlight"]:
        url = f"https://neurips.cc/virtual/{year}/{paper_type}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, 'html.parser')
            cards = soup.find_all(['div', 'li'], class_=re.compile(r'paper|card', re.I))

            for card in cards:
                title_tag = card.find(['h3', 'h4', 'a', 'span'], class_=re.compile(r'title', re.I))
                if not title_tag:
                    title_tag = card.find('a')
                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)
                link = title_tag.get('href', '') if title_tag.name == 'a' else ''
                if not link:
                    a_tag = card.find('a')
                    link = a_tag.get('href', '') if a_tag else ''

                if link and not link.startswith('http'):
                    link = urljoin("https://neurips.cc", link)

                paper = {
                    "title": title,
                    "abstract": "",
                    "conference": "NeurIPS",
                    "year": year,
                    "paper_type": paper_type,
                    "url": link,
                    "pdf_url": "",
                    "source": "neurips_website",
                }
                papers.append(paper)

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            logger.error(f"Error scraping NeurIPS {year} {paper_type}: {e}")

    return papers


# ========================
# arXiv API Scraping
# ========================

def scrape_arxiv(query: str, max_results: int = 200, date_from: str = "") -> List[Dict]:
    """
    Search arXiv API for papers matching query.
    Returns list of paper dicts in the same format as OpenReview papers.
    """
    papers = []
    base_url = "https://export.arxiv.org/api/query"

    # Build search query — use ti: (title) + abs: (abstract) for better results
    search_query = f'all:{query}'
    if date_from:
        # arXiv API doesn't support date filter directly in query,
        # we filter after fetching
        pass

    # Paginate (arXiv max 100 per request)
    batch_size = 100
    for offset in range(0, max_results, batch_size):
        params = {
            "search_query": search_query,
            "start": offset,
            "max_results": min(batch_size, max_results - offset),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        try:
            logger.info(f"Querying arXiv: query='{query}' offset={offset}")
            resp = requests.get(base_url, params=params, timeout=30)
            if resp.status_code != 200:
                logger.warning(f"arXiv returned {resp.status_code}")
                break

            soup = BeautifulSoup(resp.text, 'xml')
            entries = soup.find_all('entry')

            if not entries:
                break

            for entry in entries:
                # Parse entry
                title_tag = entry.find('title')
                title = title_tag.get_text(strip=True).replace('\n', ' ') if title_tag else ""

                abstract_tag = entry.find('summary')
                abstract = abstract_tag.get_text(strip=True).replace('\n', ' ') if abstract_tag else ""

                # Published date
                published_tag = entry.find('published')
                published = published_tag.get_text(strip=True) if published_tag else ""
                pub_year = int(published[:4]) if published and len(published) >= 4 else 0

                # Date filter
                if date_from and published < date_from:
                    continue

                # Get URLs
                arxiv_id = ""
                paper_url = ""
                pdf_url = ""

                id_tag = entry.find('id')
                if id_tag:
                    paper_url = id_tag.get_text(strip=True)
                    arxiv_id = paper_url.split('/')[-1]
                    # Remove version suffix for cleaner ID
                    if 'v' in arxiv_id and arxiv_id[-1].isdigit():
                        arxiv_id_base = arxiv_id.rsplit('v', 1)[0]
                    else:
                        arxiv_id_base = arxiv_id

                for link in entry.find_all('link'):
                    if link.get('title') == 'pdf':
                        pdf_url = link.get('href', '')
                        if pdf_url and not pdf_url.endswith('.pdf'):
                            pdf_url += '.pdf'

                if not pdf_url and arxiv_id:
                    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

                # Categories
                categories = [cat.get('term', '') for cat in entry.find_all('category')]
                primary_cat = categories[0] if categories else ""

                # Authors
                authors = [a.find('name').get_text(strip=True)
                          for a in entry.find_all('author') if a.find('name')]

                paper = {
                    "title": title,
                    "abstract": abstract,
                    "conference": "arXiv",
                    "year": pub_year,
                    "paper_type": primary_cat,
                    "url": paper_url,
                    "pdf_url": pdf_url,
                    "arxiv_id": arxiv_id,
                    "published": published,
                    "authors": authors,
                    "source": "arxiv",
                }
                papers.append(paper)

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            logger.error(f"Error querying arXiv with query '{query}': {e}")
            break

    logger.info(f"arXiv query '{query}': found {len(papers)} papers")
    return papers


def fetch_arxiv_papers() -> List[Dict]:
    """Fetch papers from arXiv for all configured queries."""
    if not ARXIV_QUERIES:
        return []

    all_papers = []
    for query in ARXIV_QUERIES:
        logger.info(f"\n{'='*60}")
        logger.info(f"Searching arXiv: {query}")
        logger.info(f"{'='*60}")

        papers = scrape_arxiv(query, max_results=ARXIV_MAX_RESULTS, date_from=ARXIV_DATE_FROM)
        all_papers.extend(papers)
        time.sleep(3)  # arXiv rate limit: be gentle

    logger.info(f"Total arXiv papers fetched: {len(all_papers)}")
    return all_papers


# ========================
# Main Scraping Logic
# ========================

def fetch_all_papers() -> List[Dict]:
    """Fetch papers from all target conferences and filter by keywords."""
    all_papers = []

    for conference, year in TARGET_CONFERENCES:
        logger.info(f"\n{'='*60}")
        logger.info(f"Scraping {conference} {year}...")
        logger.info(f"{'='*60}")

        conf_papers = []

        # Try OpenReview first (covers ICLR, NeurIPS, ICML)
        for paper_type in TARGET_PAPER_TYPES:
            papers = scrape_openreview_papers(conference, year, paper_type)
            conf_papers.extend(papers)

        # If OpenReview returned nothing, try fallback scrapers
        if not conf_papers:
            logger.info(f"OpenReview returned no results for {conference} {year}, trying fallback...")
            if conference == "ICML":
                conf_papers = scrape_icml_proceedings(year)
            elif conference == "NeurIPS":
                conf_papers = scrape_neurips_papers(year)

        logger.info(f"Total papers found for {conference} {year}: {len(conf_papers)}")
        all_papers.extend(conf_papers)

    return all_papers


def limit_candidate_papers(papers: List[Dict]) -> List[Dict]:
    """Apply a hard cap before LLM filtering to control token cost."""
    if MAX_CANDIDATE_PAPERS <= 0 or len(papers) <= MAX_CANDIDATE_PAPERS:
        return papers

    ranked = rank_papers_by_topic(papers, TOPIC)
    logger.warning(
        "Limiting candidate papers from %s to %s before LLM filtering after topic pre-ranking",
        len(papers),
        MAX_CANDIDATE_PAPERS,
    )
    return ranked[:MAX_CANDIDATE_PAPERS]


def _topic_terms(topic: str) -> list[str]:
    stop_words = {
        "the", "and", "or", "for", "with", "from", "into", "that", "this", "these", "those",
        "paper", "papers", "search", "related", "about", "using", "based", "会议", "论文",
        "搜索", "相关", "限制", "篇", "请", "中和",
    }
    terms = []
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_-]*|[\u4e00-\u9fff]{2,}", topic or ""):
        term = raw.lower().strip("-_")
        if len(term) < 2 or term in stop_words:
            continue
        terms.append(term)
    aliases = {
        "llm": ["llm", "llms", "language model", "language models"],
        "multi-agent": ["multi-agent", "multi agent", "multiagent", "agents"],
        "collaboration": ["collaboration", "cooperation", "coordination", "collaborative", "cooperative"],
        "agentic": ["agentic", "agent"],
    }
    expanded = []
    for term in terms:
        expanded.append(term)
        expanded.extend(aliases.get(term, []))
    deduped = []
    for term in expanded:
        if term not in deduped:
            deduped.append(term)
    return deduped


def rank_papers_by_topic(papers: List[Dict], topic: str) -> List[Dict]:
    """Rank papers by simple lexical topic relevance before applying a hard cap."""
    terms = _topic_terms(topic)
    if not terms:
        return papers

    def score_item(index_paper):
        index, paper = index_paper
        title = str(paper.get("title") or "").lower()
        abstract = str(paper.get("abstract") or "").lower()
        combined = f"{title}\n{abstract}"
        score = 0
        for term in terms:
            term_lower = term.lower()
            if term_lower in title:
                score += 5
            if term_lower in abstract:
                score += 1
        return (-score, index)

    ranked_pairs = sorted(enumerate(papers), key=score_item)
    return [paper for _, paper in ranked_pairs]


def save_paper_list(papers: List[Dict], filepath: str = None):
    """Save paper list to JSON file."""
    if filepath is None:
        filepath = PAPER_LIST_FILE
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(papers, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(papers)} papers to {filepath}")


def load_paper_list(filepath: str = None) -> List[Dict]:
    """Load paper list from JSON file."""
    if filepath is None:
        filepath = PAPER_LIST_FILE
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def run_scraper():
    """Main entry point for paper scraping."""
    logger.info("Starting paper scraper...")

    if OPENREVIEW_API_TOKEN:
        logger.info("Using authenticated OpenReview API requests.")
    else:
        logger.info("Using public OpenReview API (no token). Set OPENREVIEW_API_TOKEN for higher rate limits.")

    # Fetch all papers from conferences
    all_papers = fetch_all_papers()
    logger.info(f"\nTotal papers fetched (conferences): {len(all_papers)}")

    # Fetch from arXiv
    arxiv_papers = fetch_arxiv_papers()
    if arxiv_papers:
        logger.info(f"Total papers fetched (arXiv): {len(arxiv_papers)}")
        all_papers.extend(arxiv_papers)

    # Deduplicate by title first (before expensive LLM calls)
    seen_titles = set()
    deduped_papers = []
    for paper in all_papers:
        title_lower = paper["title"].lower().strip()
        if title_lower not in seen_titles:
            seen_titles.add(title_lower)
            deduped_papers.append(paper)
    logger.info(f"Unique papers after dedup: {len(deduped_papers)}")

    deduped_papers = limit_candidate_papers(deduped_papers)
    logger.info(f"Candidate papers after hard limit: {len(deduped_papers)}")

    # Save all papers (before filtering)
    all_papers_file = os.path.join(OUTPUT_DIR, "all_papers_raw.json")
    save_paper_list(deduped_papers, all_papers_file)

    # Filter by topic using LLM
    matched_papers = filter_papers_with_llm(deduped_papers, TOPIC, FILTER_PROMPT_TEMPLATE)
    logger.info(f"Papers matching topic after LLM filtering: {len(matched_papers)}")

    # Save filtered paper list
    save_paper_list(matched_papers)

    # Print summary
    print(f"\n{'='*80}")
    print(f"PAPER SCRAPING COMPLETE")
    print(f"{'='*80}")
    print(f"Total papers fetched: {len(all_papers)}")
    print(f"  Conferences: {len(all_papers) - len(arxiv_papers)}")
    print(f"  arXiv: {len(arxiv_papers)}")
    print(f"Unique papers (after dedup): {len(deduped_papers)}")
    print(f"Matching papers (after LLM filter): {len(matched_papers)}")
    print(f"Topic: {TOPIC}")
    print(f"\nPaper list saved to: {PAPER_LIST_FILE}")
    print(f"\nBreakdown by conference:")
    conf_counts = Counter(f"{p['conference']} {p['year']} ({p['paper_type']})" for p in matched_papers)
    for conf, count in sorted(conf_counts.items()):
        print(f"  {conf}: {count}")

    return matched_papers
