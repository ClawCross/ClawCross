"""
Step 2: Download PDFs for scraped papers.
Supports downloading from OpenReview, arXiv, and direct URLs.
"""

import json
import os
import re
import time
import logging
import threading
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from tqdm import tqdm

from paper_survey.config import (
    PDFS_DIR, PAPER_LIST_FILE, MAX_CONCURRENT_DOWNLOADS,
    DOWNLOAD_TIMEOUT, REQUEST_DELAY
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}

_thread_local = threading.local()


def _get_session() -> requests.Session:
    """Create one requests session per worker thread for connection reuse."""
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session
    return session


def sanitize_filename(title: str) -> str:
    """Convert paper title to a safe filename."""
    clean = re.sub(r'[^\w\s-]', '', title)
    clean = re.sub(r'\s+', '_', clean.strip())
    if len(clean) > 100:
        clean = clean[:100]
    return clean


def get_pdf_path(paper: Dict) -> str:
    """Get the local PDF file path for a paper."""
    conf = paper.get("conference", "unknown")
    year = paper.get("year", "unknown")
    title = paper.get("title", "unknown")
    filename = f"{conf}{year}_{sanitize_filename(title)}.pdf"
    conf_dir = os.path.join(PDFS_DIR, f"{conf}_{year}")
    os.makedirs(conf_dir, exist_ok=True)
    return os.path.join(conf_dir, filename)


def try_download_pdf(url: str, save_path: str) -> bool:
    """Try to download a PDF from a URL."""
    try:
        session = _get_session()
        resp = session.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
        if resp.status_code == 200:
            content_type = resp.headers.get('content-type', '')
            if 'pdf' in content_type.lower() or url.endswith('.pdf'):
                with open(save_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                if os.path.getsize(save_path) > 1000:
                    return True
                else:
                    os.remove(save_path)
            else:
                header = next(resp.iter_content(chunk_size=4), b'')
                if header == b'%PDF':
                    with open(save_path, 'wb') as f:
                        f.write(header)
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    if os.path.getsize(save_path) > 1000:
                        return True
                    os.remove(save_path)
        return False
    except Exception as e:
        logger.debug(f"Failed to download from {url}: {e}")
        return False


def get_arxiv_pdf_url(paper: Dict) -> Optional[str]:
    """Try to find arxiv PDF URL by searching paper title."""
    title = paper.get("title", "")
    if not title:
        return None

    try:
        search_url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": f'ti:"{title}"',
            "start": 0,
            "max_results": 3,
        }
        resp = _get_session().get(search_url, params=params, timeout=15)
        if resp.status_code != 200:
            return None

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, 'xml')
        entries = soup.find_all('entry')

        for entry in entries:
            entry_title = entry.find('title')
            if entry_title:
                entry_title_text = entry_title.get_text(strip=True)
                if _titles_match(title, entry_title_text):
                    for link in entry.find_all('link'):
                        if link.get('title') == 'pdf':
                            return link.get('href') + '.pdf'
                    arxiv_id = entry.find('id')
                    if arxiv_id:
                        aid = arxiv_id.get_text(strip=True).split('/')[-1]
                        return f"https://arxiv.org/pdf/{aid}.pdf"

    except Exception as e:
        logger.debug(f"ArXiv search failed for '{title}': {e}")

    return None


def _titles_match(title1: str, title2: str) -> bool:
    """Check if two paper titles are similar enough."""
    def normalize(t):
        return re.sub(r'[^\w]', '', t.lower())
    n1, n2 = normalize(title1), normalize(title2)
    if n1 == n2:
        return True
    if len(n1) > 10 and len(n2) > 10:
        shorter, longer = (n1, n2) if len(n1) < len(n2) else (n2, n1)
        if shorter in longer or longer in shorter:
            return True
        common = sum(1 for c in shorter if c in longer)
        ratio = common / max(len(shorter), 1)
        return ratio > 0.85
    return False


def download_paper_pdf(paper: Dict) -> Dict:
    """Download PDF for a single paper. Returns updated paper dict."""
    pdf_path = get_pdf_path(paper)

    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
        paper["pdf_local_path"] = pdf_path
        paper["pdf_downloaded"] = True
        return paper

    urls_to_try = []

    pdf_url = paper.get("pdf_url", "")
    if pdf_url:
        urls_to_try.append(pdf_url)

    openreview_id = paper.get("openreview_id", "")
    if openreview_id:
        urls_to_try.append(f"https://openreview.net/pdf?id={openreview_id}")

    arxiv_url = get_arxiv_pdf_url(paper)
    if arxiv_url:
        urls_to_try.append(arxiv_url)

    for url in urls_to_try:
        if try_download_pdf(url, pdf_path):
            paper["pdf_local_path"] = pdf_path
            paper["pdf_downloaded"] = True
            paper["pdf_source_url"] = url
            logger.info(f"Downloaded: {paper.get('title', 'unknown')[:60]}...")
            return paper
        time.sleep(1)

    paper["pdf_downloaded"] = False
    paper["pdf_local_path"] = ""
    logger.warning(f"Failed to download: {paper.get('title', 'unknown')[:60]}...")
    return paper


def download_all_papers(papers: List[Dict] = None) -> List[Dict]:
    """Download PDFs for all papers."""
    if papers is None:
        if not os.path.exists(PAPER_LIST_FILE):
            logger.error(f"Paper list not found: {PAPER_LIST_FILE}. Run scraper first.")
            return []
        with open(PAPER_LIST_FILE, 'r', encoding='utf-8') as f:
            papers = json.load(f)

    logger.info(f"Downloading PDFs for {len(papers)} papers...")
    os.makedirs(PDFS_DIR, exist_ok=True)

    updated_papers = [None] * len(papers)

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_DOWNLOADS) as executor:
        futures = {
            executor.submit(download_paper_pdf, paper): (index, paper)
            for index, paper in enumerate(papers)
        }

        with tqdm(total=len(papers), desc="Downloading PDFs") as pbar:
            for future in as_completed(futures):
                index, paper = futures[future]
                try:
                    result = future.result()
                    updated_papers[index] = result
                except Exception as e:
                    paper["pdf_downloaded"] = False
                    updated_papers[index] = paper
                    logger.error(f"Error downloading {paper.get('title', 'unknown')}: {e}")
                pbar.update(1)

    with open(PAPER_LIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(updated_papers, f, indent=2, ensure_ascii=False)

    downloaded = sum(1 for p in updated_papers if p.get("pdf_downloaded"))
    failed = len(updated_papers) - downloaded
    print(f"\n{'='*60}")
    print(f"PDF DOWNLOAD COMPLETE")
    print(f"{'='*60}")
    print(f"Downloaded: {downloaded}/{len(updated_papers)}")
    print(f"Failed: {failed}")
    print(f"PDFs saved to: {PDFS_DIR}")

    return updated_papers
