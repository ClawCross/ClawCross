"""Batch-analyze PDF files in a folder with parallel LLM processing."""

from __future__ import annotations

import argparse
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List

from tqdm import tqdm

import paper_survey.config as config
from paper_survey.analyzer import extract_text_from_pdf
from paper_survey.llm import send_to_llm

logger = logging.getLogger(__name__)

DEFAULT_ANALYSIS_PROMPT = """You are an expert research assistant reading an academic PDF.
Produce a detailed Markdown report with this structure:

## 1. Basic Information
- Title
- Authors if identifiable
- File name

## 2. TL;DR
- A concise 3-5 sentence summary

## 3. Research Problem
- What problem is the paper solving?
- Why does it matter?

## 4. Core Method
- Main idea
- Key technical design choices

## 5. Experiments And Evidence
- Datasets, tasks, or benchmarks
- Main empirical results
- Important ablations or comparisons

## 6. Strengths
- Most convincing parts of the paper

## 7. Limitations
- Important weaknesses, caveats, or missing evidence

## 8. Takeaways
- 3-5 concrete takeaways

Be specific, grounded in the paper text, and avoid fluff."""


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def _safe_stem(name: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", name)
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    return cleaned[:120] or "report"


def _guess_title(pdf_path: Path) -> str:
    return pdf_path.stem.replace("_", " ").replace("-", " ").strip() or pdf_path.name


def _split_text_chunks(text: str, max_chars: int) -> List[str]:
    """Split extracted text into chunks without dropping content."""
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    current_parts: List[str] = []
    current_len = 0

    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        if len(paragraph) > max_chars:
            if current_parts:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_len = 0
            for start in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[start:start + max_chars])
            continue

        separator_len = 2 if current_parts else 0
        if current_parts and current_len + separator_len + len(paragraph) > max_chars:
            chunks.append("\n\n".join(current_parts))
            current_parts = [paragraph]
            current_len = len(paragraph)
            continue

        current_parts.append(paragraph)
        current_len += separator_len + len(paragraph)

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks or [text]


def _call_llm(title: str, context: str, task: str) -> str:
    return send_to_llm(
        messages=[
            {
                "role": "system",
                "content": "You are an expert research assistant performing close reading of academic PDFs. "
                           "Be accurate, specific, and cite details from the provided text.",
            },
            {
                "role": "user",
                "content": f"""Paper title: {title}

Task:
{task}

Paper text:
{context}
""",
            },
        ],
        tag="pdf_folder_batch",
        temperature=0.2,
    )


def _analyze_pdf_text(
    title: str,
    paper_text: str,
    analysis_prompt: str,
    chunk_chars: int,
) -> tuple[str, int]:
    chunks = _split_text_chunks(paper_text, chunk_chars)

    if len(chunks) == 1:
        return _call_llm(title, chunks[0], analysis_prompt), 1

    partial_reports: List[str] = []
    total_chunks = len(chunks)
    for index, chunk in enumerate(chunks, 1):
        chunk_task = (
            "Read this chunk of the paper carefully and produce detailed analysis notes. "
            f"This is chunk {index} of {total_chunks}. Focus only on evidence present in this chunk. "
            "Capture methods, experiments, claims, assumptions, and limitations mentioned here."
        )
        partial_reports.append(_call_llm(title, chunk, chunk_task))

    merged_context = "\n\n---\n\n".join(
        f"### Chunk Notes {index}\n{report}"
        for index, report in enumerate(partial_reports, 1)
    )
    merge_task = (
        f"{analysis_prompt}\n\n"
        "Merge the chunk-level notes into one final report. Deduplicate repeated points, preserve concrete "
        "details, and resolve conflicts conservatively."
    )
    return _call_llm(title, merged_context, merge_task), total_chunks


def _write_report(output_path: Path, pdf_path: Path, title: str, report: str) -> None:
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(f"# PDF Report: {title}\n\n")
        handle.write(f"- **Source File:** {pdf_path}\n\n")
        handle.write("---\n\n")
        handle.write(report)
        handle.write("\n")


def analyze_single_pdf(
    pdf_path: Path,
    output_dir: Path,
    analysis_prompt: str,
    chunk_chars: int,
    overwrite: bool,
) -> Dict:
    title = _guess_title(pdf_path)
    output_path = output_dir / f"{_safe_stem(pdf_path.stem)}.md"

    if output_path.exists() and not overwrite:
        return {
            "pdf_path": str(pdf_path),
            "report_path": str(output_path),
            "title": title,
            "status": "skipped",
        }

    paper_text = extract_text_from_pdf(str(pdf_path))
    if not paper_text.strip():
        return {
            "pdf_path": str(pdf_path),
            "report_path": str(output_path),
            "title": title,
            "status": "extract_failed",
        }

    report, chunk_count = _analyze_pdf_text(title, paper_text, analysis_prompt, chunk_chars)
    _write_report(output_path, pdf_path, title, report)

    return {
        "pdf_path": str(pdf_path),
        "report_path": str(output_path),
        "title": title,
        "status": "ok",
        "chars_extracted": len(paper_text),
        "chunk_count": chunk_count,
    }


def collect_pdf_files(input_dir: Path, recursive: bool) -> List[Path]:
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(path for path in input_dir.glob(pattern) if path.is_file())


def run_pdf_folder_batch(
    input_dir: Path,
    output_dir: Path,
    recursive: bool,
    overwrite: bool,
    workers: int,
    chunk_chars: int,
    analysis_prompt: str,
) -> List[Dict]:
    pdf_files = collect_pdf_files(input_dir, recursive)
    if not pdf_files:
        raise SystemExit(f"No PDF files found in {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    results: List[Dict] = [None] * len(pdf_files)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                analyze_single_pdf,
                pdf_path,
                output_dir,
                analysis_prompt,
                chunk_chars,
                overwrite,
            ): index
            for index, pdf_path in enumerate(pdf_files)
        }

        with tqdm(total=len(pdf_files), desc="Analyzing PDFs") as progress:
            for future in as_completed(futures):
                index = futures[future]
                pdf_path = pdf_files[index]
                try:
                    results[index] = future.result()
                except Exception as exc:
                    logger.exception("PDF analysis failed for %s", pdf_path)
                    results[index] = {
                        "pdf_path": str(pdf_path),
                        "title": _guess_title(pdf_path),
                        "status": "failed",
                        "error": str(exc),
                    }
                progress.update(1)

    summary_path = output_dir / "batch_results.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, ensure_ascii=False)

    ok_count = sum(1 for item in results if item["status"] == "ok")
    skipped_count = sum(1 for item in results if item["status"] == "skipped")
    failed_count = len(results) - ok_count - skipped_count

    print(f"\n{'=' * 60}")
    print("PDF FOLDER ANALYSIS COMPLETE")
    print(f"{'=' * 60}")
    print(f"Input directory: {input_dir}")
    print(f"Output directory: {output_dir}")
    print(f"PDF files found: {len(pdf_files)}")
    print(f"Analyzed: {ok_count}")
    print(f"Skipped: {skipped_count}")
    print(f"Failed: {failed_count}")
    print(f"Summary JSON: {summary_path}")

    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paper-survey-pdf-folder",
        description="Batch analyze all PDF files in a folder with parallel LLM processing.",
    )
    parser.add_argument("input_dir", help="Folder containing PDF files")
    parser.add_argument("--output-dir", help="Directory for markdown reports and batch_results.json")
    parser.add_argument("--recursive", action="store_true", help="Recursively scan subdirectories for PDFs")
    parser.add_argument("--overwrite", action="store_true", help="Re-analyze files even if report already exists")
    parser.add_argument("--workers", type=int, default=config.MAX_CONCURRENT_ANALYSIS,
                        help="Parallel analysis workers")
    parser.add_argument("--chunk-chars", type=int, default=45000,
                        help="Max characters per LLM chunk before hierarchical merge")
    parser.add_argument("--prompt-file", help="Path to a custom analysis prompt text file")
    parser.add_argument("--api-key", help="Override llm_api_key from runtime_config.json")
    parser.add_argument("--base-url", help="Override llm_base_url from runtime_config.json")
    parser.add_argument("--model", help="Override llm_model from runtime_config.json")
    parser.add_argument("--persona-tag", help="Override clawcross_persona_tag for ClawCross persona backend")
    return parser


def main() -> None:
    _setup_logging()
    parser = build_parser()
    args = parser.parse_args()

    overrides = {}
    if args.api_key:
        overrides["api_key"] = args.api_key
    if args.base_url:
        overrides["base_url"] = args.base_url
    if args.model:
        overrides["model"] = args.model
    if args.persona_tag:
        overrides["clawcross_persona_tag"] = args.persona_tag
    if overrides:
        config.override(**overrides)

    config.validate()

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist or is not a directory: {input_dir}")

    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else input_dir / "analysis_reports"

    analysis_prompt = DEFAULT_ANALYSIS_PROMPT
    if args.prompt_file:
        analysis_prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")

    run_pdf_folder_batch(
        input_dir=input_dir,
        output_dir=output_dir,
        recursive=args.recursive,
        overwrite=args.overwrite,
        workers=max(1, args.workers),
        chunk_chars=max(1000, args.chunk_chars),
        analysis_prompt=analysis_prompt,
    )


if __name__ == "__main__":
    main()
