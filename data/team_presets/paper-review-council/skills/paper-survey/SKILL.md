---
name: paper-survey
description: Scrape papers from OpenReview/arXiv, filter with an LLM, analyze papers or local PDFs, and generate a survey report.
category: research
platform: clawcross
---

# Paper Survey

Use this skill when the team needs to search academic papers, filter candidate papers by topic, analyze PDFs, or generate a literature survey.

This skill is self-contained. All paths below are relative to this skill directory:

`data/user_files/<user>/teams/<team>/skills/paper-survey`

## Setup

From the skill directory:

```bash
cp runtime_config.example.json runtime_config.json
```

By default this skill prefers ClawCross `send_persona`, so it can run without a direct LLM API key when ClawCross/OASIS is running.

## External Agent Runbook

Use these steps when another agent needs to run this skill from ClawCross.

1. Enter the skill directory. This is the only path decision the caller needs to make.

```bash
cd data/user_files/<user>/teams/<team>/skills/paper-survey
```

2. Run the stable entrypoint. Do not choose a Python interpreter, do not set `PYTHONPATH`, do not install the package, and do not modify any virtual environment.

```bash
./run.sh --help
```

`run.sh` automatically locates the ClawCross root, prefers the repo-local `.venv/bin/python` when present, falls back to `python3` or `python`, then dispatches to `run.py`. It also supports the local PDF subcommand: `./run.sh pdf-folder ...`.

3. If `runtime_config.json` does not exist, create it from the example:

```bash
cp runtime_config.example.json runtime_config.json
```

Keep `clawcross_persona_enabled=true` unless the caller explicitly wants to use a direct OpenAI-compatible API key. With the persona backend enabled, the skill sends LLM work to the team persona and does not require the external agent to provide an API key.

Leave `clawcross_user_id` and `clawcross_team` empty to infer them from the skill path. Only set them when intentionally routing to a different user or team.

The persona used by the skill can be selected at runtime:

```bash
./run.sh --all --lite --persona-tag ml_reviewer --topic "LLM multi-agent collaboration"
```

If `--persona-tag` is omitted, the skill uses `clawcross_persona_tag` from `runtime_config.json`; if that is empty, it defaults to `paper_reporter`.

4. Minimal smoke test with a hard cap of 10 candidate papers:

```bash
rm -rf /tmp/paper-survey-test-10
./run.sh --all --lite \
  --conferences 'ICLR:2024' \
  --max-papers 10 \
  --persona-tag ml_reviewer \
  --output-dir /tmp/paper-survey-test-10
```

Expected result:

- `all_papers_raw.json` contains at most 10 papers.
- `paper_list.json` contains the LLM-filtered subset.
- `survey_report.md` is generated.

5. Verify output counts without requiring `jq`:

```bash
./run.sh inspect-output /tmp/paper-survey-test-10
```

Recommended config for this team:

```json
{
  "clawcross_persona_enabled": true,
  "clawcross_user_id": "",
  "clawcross_team": "",
  "clawcross_persona_tag": "paper_reporter",
  "clawcross_fallback_to_openai": true,
  "clawcross_persona_timeout": 120,
  "llm_api_key": "",
  "llm_base_url": "https://api.openai.com/v1",
  "llm_model": "gpt-4o-mini"
}
```

`llm_api_key` is only needed when `clawcross_persona_enabled` is false, or when the ClawCross persona backend fails and you want OpenAI-compatible fallback.

Use the stable entrypoint from this skill directory:

```bash
./run.sh --help
```

Do not install this skill into the environment unless you are intentionally developing packaging.

## Main Survey Pipeline

### Preferred LLM Backend: ClawCross Persona

The code path uses `paper_survey/llm.py::send_to_llm(...)`.

Priority order:

1. If `clawcross_persona_enabled=true`, call `oasis.agent_center.send_team_persona(...)`.
2. Infer `clawcross_user_id` and `clawcross_team` from the skill path when they are empty.
3. Use `clawcross_persona_tag` to choose the persona.
4. A CLI `--persona-tag <tag>` overrides `clawcross_persona_tag` for that run.
5. If persona calling fails and `clawcross_fallback_to_openai=true` with `llm_api_key` present, fall back to OpenAI-compatible API.
6. If fallback is disabled or no key is configured, fail clearly.

For this team, use:

```json
{
  "clawcross_persona_enabled": true,
  "clawcross_user_id": "",
  "clawcross_team": "",
  "clawcross_persona_tag": "paper_reporter",
  "clawcross_persona_timeout": 120
}
```

Run the full lite pipeline, using abstracts only:

```bash
./run.sh --all --lite \
  --topic "LLM multi-agent collaboration" \
  --arxiv "LLM multi-agent collaboration,agentic AI collaboration" \
  --persona-tag ml_reviewer \
  --max-papers 100 \
  --output-dir ./output
```

Run full PDF-based analysis:

```bash
./run.sh --all --full \
  --topic "world models and learned simulators" \
  --arxiv "world model reinforcement learning,learned simulator" \
  --max-papers 50 \
  --output-dir ./output
```

Resume from a step:

```bash
./run.sh --from-step analyze --output-dir ./output
```

## Local PDF Folder Analysis

Analyze all PDFs in a folder:

```bash
./run.sh pdf-folder /path/to/pdf_dir \
  --recursive \
  --persona-tag statistics_reviewer \
  --workers 4 \
  --output-dir ./output/pdf_reports
```

Use a custom prompt:

```bash
./run.sh pdf-folder /path/to/pdf_dir \
  --prompt-file ./my_pdf_prompt.txt \
  --output-dir ./output/pdf_reports
```

## Python API

```python
from paper_survey.api import configure, scrape_papers, run_pipeline, analyze_pdf_folder

configure(
    api_key="your_api_key",
    base_url="https://api.openai.com/v1",
    model="gpt-4o-mini",
    topic="LLM multi-agent collaboration",
    arxiv_queries=["LLM multi-agent collaboration"],
    max_candidate_papers=100,
)

papers = scrape_papers()
run_pipeline(from_step="analyze")
```

## Important Notes

- The primary config file is `runtime_config.json` in this skill directory.
- `runtime_config.example.json` is safe to commit; never commit a real `runtime_config.json` with secrets.
- Prefer `clawcross_persona_enabled=true` inside ClawCross to avoid direct API key configuration.
- Lite mode avoids PDF download and only analyzes title/abstract.
- Full mode downloads PDFs and needs PDF extraction dependencies such as `pdfplumber` or `PyPDF2`.
- LLM filtering is fail-open: if the filter call fails, candidates are kept. Use `max_candidate_papers` to control cost.
- Default concurrency can be high. Reduce `max_candidate_papers`, `--workers`, or config concurrency if rate-limited.

## Outputs

Default outputs are written under `./output` from this skill directory unless `--output-dir` is provided:

- `output/paper_list.json`
- `output/reports/`
- `output/survey_report.md`
- `logs/` or the configured log directory
