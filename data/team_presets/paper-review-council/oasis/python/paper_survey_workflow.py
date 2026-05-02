import asyncio
import json
import os
import re
import sys
from pathlib import Path

try:
    from oasis.python_workflow_cli import StandaloneWorkflowContext, run_cli
except ModuleNotFoundError:
    extra_paths = [
        p for p in os.environ.get("CLAWCROSS_PYTHONPATH", "").split(os.pathsep) if p
    ]
    project_root = os.environ.get("CLAWCROSS_PROJECT_ROOT", "").strip()
    if project_root:
        extra_paths.append(project_root)
    for parent in Path(__file__).resolve().parents:
        if (parent / "oasis" / "python_workflow_cli.py").is_file() and (parent / "src").is_dir():
            extra_paths.append(str(parent))
            break
    for path_entry in extra_paths:
        if path_entry and path_entry not in sys.path:
            sys.path.insert(0, path_entry)
    from oasis.python_workflow_cli import StandaloneWorkflowContext, run_cli


DOMAIN_EXPERT_TAGS = {
    "ml_reviewer",
    "systems_reviewer",
    "biomed_reviewer",
    "econ_finance_reviewer",
    "social_science_reviewer",
    "statistics_reviewer",
    "humanities_reviewer",
}


def _team_dir(ctx: StandaloneWorkflowContext) -> Path:
    return Path(__file__).resolve().parents[2]


def _skill_dir(ctx: StandaloneWorkflowContext) -> Path:
    return _team_dir(ctx) / "skills" / "paper-survey"


def _output_dir(ctx: StandaloneWorkflowContext) -> Path:
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", ctx.run_id or "run")
    return _skill_dir(ctx) / "output" / "workflow_runs" / safe_run_id


def _expert_list(ctx: StandaloneWorkflowContext) -> list[dict]:
    personas = ctx.list_personas()
    experts = []
    for persona in personas:
        tag = str(persona.get("tag") or persona.get("id") or "").strip()
        if tag not in DOMAIN_EXPERT_TAGS:
            continue
        experts.append(
            {
                "tag": tag,
                "name": persona.get("name") or tag,
                "summary": str(persona.get("persona") or "")[:260],
            }
        )
    return sorted(experts, key=lambda item: item["tag"])


def _parse_selected_tags(text: str, available_tags: set[str]) -> list[str]:
    selected: list[str] = []
    match = re.search(r"选择专家\s*[:：]\s*([^\n]+)", text or "")
    source = match.group(1) if match else text
    for tag in re.findall(r"[A-Za-z][A-Za-z0-9_]*", source or ""):
        if tag in available_tags and tag not in selected:
            selected.append(tag)
    return selected[:3]


def _parse_commander_topic(text: str) -> str:
    patterns = [
        r"--topic\s+['\"]([^'\"]+)['\"]",
        r"(?:核心关键词|搜索主题|topic|主题)\s*[:：]\s*([^\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if not match:
            continue
        value = match.group(1).strip()
        value = re.sub(r"[*`#]+", "", value).strip()
        value = re.split(r"\s{2,}|[;；]", value)[0].strip()
        if value:
            return value
    return ""


def _parse_commander_value(text: str, key: str) -> str:
    match = re.search(rf"{re.escape(key)}\s*[=:：]\s*([^\n;；]+)", text or "", re.I)
    return match.group(1).strip() if match else ""


def _parse_request_options(question: str) -> dict:
    text = question or ""
    max_papers = 10
    match = re.search(r"(?:max[-_ ]?papers|论文数|候选论文数)\s*[=:：为]?\s*(\d+)", text, re.I)
    if match:
        max_papers = max(1, min(500, int(match.group(1))))

    conferences = ""
    match = re.search(r"(?:conferences|会议)\s*[=:：]\s*([A-Za-z0-9:_,，\s-]+)", text, re.I)
    if match:
        conferences = match.group(1).replace("，", ",").strip()
        conferences = re.sub(r"\s+", "", conferences)

    arxiv = ""
    match = re.search(r"(?:arxiv|arXiv)\s*[=:：]\s*([^\n;；]+)", text)
    if match:
        arxiv = match.group(1).strip()

    lite = not bool(re.search(r"\bfull\b|全文|PDF|pdf|下载", text, re.I))
    return {
        "topic": text.strip() or "multi-agent systems",
        "max_papers": max_papers,
        "conferences": conferences,
        "arxiv": arxiv,
        "lite": lite,
    }


def _interesting_log_line(line: str) -> bool:
    markers = [
        "Total papers found",
        "Total papers fetched",
        "Unique papers after dedup",
        "Limiting candidate papers",
        "Candidate papers after hard limit",
        "Saved ",
        "LLM filtering",
        "Papers matching topic",
        "PAPER SCRAPING COMPLETE",
        "PAPER ANALYSIS COMPLETE",
        "SURVEY GENERATION COMPLETE",
        "PIPELINE COMPLETE",
        "Survey saved to",
        "No papers found",
    ]
    return any(marker in line for marker in markers)


async def _run_skill(ctx: StandaloneWorkflowContext, skill_dir: Path, args: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "./run.sh",
        *args,
        cwd=str(skill_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    collected: list[str] = []
    progress_buffer: list[str] = []
    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip()
        if not line:
            continue
        collected.append(line)
        clean_line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
        if _interesting_log_line(clean_line):
            progress_buffer.append(clean_line)
        if len(progress_buffer) >= 6:
            await ctx.publish("\n".join(progress_buffer), author="paper-survey")
            progress_buffer.clear()

    exit_code = await proc.wait()
    if progress_buffer:
        await ctx.publish("\n".join(progress_buffer), author="paper-survey")
    return exit_code, "\n".join(collected)


def _load_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


async def main(ctx: StandaloneWorkflowContext):
    skill_dir = _skill_dir(ctx)
    if not (skill_dir / "run.sh").is_file():
        raise RuntimeError(f"paper-survey skill not found: {skill_dir}")

    experts = _expert_list(ctx)
    available_tags = {item["tag"] for item in experts}
    request_options = _parse_request_options(ctx.question)

    skill_usage = (
        "Skill 调用方式（workflow 会执行，不要输出危险 shell）：\n"
        "1. cd data/user_files/<user>/teams/<team>/skills/paper-survey\n"
        "2. ./run.sh --all --lite --topic '<topic>' --max-papers <N> --persona-tag <专家tag> --output-dir <dir>\n"
        "3. 可选参数：--conferences 'ICLR:2024,ICML:2025'，--arxiv '<query1>,<query2>'，--full\n"
        "4. 报告文件：<output-dir>/survey_report.md；候选列表：<output-dir>/all_papers_raw.json；过滤后列表：<output-dir>/paper_list.json\n"
    )
    commander_prompt = (
        f"用户启动工作流的要求：\n{ctx.question}\n\n"
        f"已解析参数：\n{json.dumps(request_options, ensure_ascii=False, indent=2)}\n\n"
        f"可用专家人设列表：\n{json.dumps(experts, ensure_ascii=False, indent=2)}\n\n"
        f"{skill_usage}\n"
        "请输出选择专家和搜索重点。必须包含一行：选择专家: tag1, tag2, ...\n"
        "选择 1-3 个最合适的领域专家；如果涉及实验/数据/因果/统计显著性，加入 statistics_reviewer。"
    )
    commander_reply = await ctx.send_persona("search_commander", commander_prompt)
    commander_text = commander_reply.content or ""
    await ctx.publish(commander_text or "(搜索指挥者无输出)", author="搜索指挥者")

    selected_tags = _parse_selected_tags(commander_text, available_tags)
    if not selected_tags and "ml_reviewer" in available_tags:
        selected_tags = ["ml_reviewer"]
    if not selected_tags and available_tags:
        selected_tags = [sorted(available_tags)[0]]
    if not selected_tags:
        selected_tags = ["paper_reporter"]

    primary_tag = selected_tags[0]
    commander_topic = _parse_commander_topic(commander_text)
    commander_conferences = _parse_commander_value(commander_text, "conferences")
    commander_arxiv = _parse_commander_value(commander_text, "arxiv")
    skill_topic = commander_topic or request_options["topic"]
    skill_conferences = commander_conferences or request_options["conferences"]
    skill_arxiv = commander_arxiv or request_options["arxiv"]
    output_dir = _output_dir(ctx)
    args = [
        "--all",
        "--lite" if request_options["lite"] else "--full",
        "--topic",
        skill_topic,
        "--max-papers",
        str(request_options["max_papers"]),
        "--persona-tag",
        primary_tag,
        "--output-dir",
        str(output_dir),
    ]
    if skill_conferences:
        args.extend(["--conferences", skill_conferences])
    if skill_arxiv:
        args.extend(["--arxiv", skill_arxiv])

    await ctx.publish(
        "开始执行 paper-survey skill：\n"
        f"persona_tag={primary_tag}\n"
        f"topic={skill_topic}\n"
        f"output_dir={output_dir}\n"
        f"args={json.dumps(args, ensure_ascii=False)}",
        author="workflowpy",
    )
    exit_code, log_text = await _run_skill(ctx, skill_dir, args)
    await ctx.publish(
        f"paper-survey exit_code={exit_code}\n\n{log_text[-4000:]}",
        author="paper-survey",
    )
    if exit_code != 0:
        raise RuntimeError(f"paper-survey skill failed with exit code {exit_code}")

    paper_list = _load_json(output_dir / "paper_list.json") or []
    raw_papers = _load_json(output_dir / "all_papers_raw.json") or []
    survey_path = output_dir / "survey_report.md"
    survey_exists = survey_path.exists()
    survey_text = survey_path.read_text(encoding="utf-8", errors="replace") if survey_exists else ""
    survey_excerpt = survey_text[:6000]

    expert_reviews = []
    for tag in selected_tags:
        review_prompt = (
            f"用户要求：\n{ctx.question}\n\n"
            f"搜索指挥者输出：\n{commander_text}\n\n"
            f"paper-survey survey_report.md 是否存在：{survey_exists}\n"
            f"报告路径（存在时才可引用为报告文件）：{survey_path}\n"
            f"候选论文数：{len(raw_papers)}；过滤后论文数：{len(paper_list)}\n\n"
            f"报告摘录：\n{survey_excerpt}\n\n"
            "请基于你的领域人设补充审读意见，重点指出该领域的关键论文、方法风险、证据强弱和后续应补充搜索的方向。"
        )
        reply = await ctx.send_persona(tag, review_prompt)
        expert_reviews.append({"tag": tag, "content": reply.content or "", "ok": reply.ok, "error": reply.error})
        await ctx.publish(reply.content or f"(empty reply; error={reply.error})", author=tag)

    reporter_prompt = (
        f"用户要求：\n{ctx.question}\n\n"
        f"搜索指挥者输出：\n{commander_text}\n\n"
        f"脚本使用的主 persona_tag：{primary_tag}\n"
        f"全部选择专家：{', '.join(selected_tags)}\n"
        f"候选论文数：{len(raw_papers)}；过滤后论文数：{len(paper_list)}\n"
        f"survey_report.md 是否存在：{survey_exists}\n"
        f"报告文件路径（仅当存在时引用为已生成报告）：{survey_path}\n"
        f"paper_list 路径：{output_dir / 'paper_list.json'}\n"
        f"raw list 路径：{output_dir / 'all_papers_raw.json'}\n\n"
        f"报告摘录：\n{survey_excerpt}\n\n"
        f"领域专家补充：\n{json.dumps(expert_reviews, ensure_ascii=False, indent=2)}\n\n"
        "请给用户最终汇报：说明搜索怎么执行、用了哪个专家 persona、核心发现、风险、下一步。"
        "如果 survey_report.md 不存在，必须明确说明本次没有生成 survey_report.md，只附上实际存在的 JSON 文件路径。"
    )
    reporter_reply = await ctx.send_persona("paper_reporter", reporter_prompt)
    await ctx.publish(reporter_reply.content or "(论文汇报者无输出)", author="论文汇报者")

    ctx.set_result(
        {
            "ok": True,
            "skill_dir": str(skill_dir),
            "output_dir": str(output_dir),
            "survey_report": str(survey_path) if survey_exists else "",
            "survey_report_exists": survey_exists,
            "paper_list": str(output_dir / "paper_list.json"),
            "all_papers_raw": str(output_dir / "all_papers_raw.json"),
            "selected_personas": selected_tags,
            "primary_persona": primary_tag,
            "raw_count": len(raw_papers),
            "filtered_count": len(paper_list),
            "reporter": reporter_reply.content or "",
        }
    )
    if survey_exists:
        ctx.set_conclusion(f"paper-survey workflow finished; report: {survey_path}")
    else:
        ctx.set_conclusion(f"paper-survey workflow finished; no survey_report.md generated; output: {output_dir}")


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
