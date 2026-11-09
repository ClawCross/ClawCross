#!/usr/bin/env python3
"""Build ClawCross team presets from alchaincyf/nuwa-skill ecosystem.

Reads SKILL.md files from cloned repos under /tmp/nuwa-skills/,
extracts key persona sections, and generates team preset directories
under data/team_presets/.

Usage:
    python tools/build_nuwa_presets.py [--source /tmp/nuwa-skills]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRESET_ROOT = PROJECT_ROOT / "data" / "team_presets"
DEFAULT_SOURCE = Path("/tmp/nuwa-skills")

# ── Persona registry ──────────────────────────────────────────

PERSONAS: list[dict[str, Any]] = [
    {
        "repo": "paul-graham-skill",
        "tag": "paul_graham",
        "name": "Paul Graham",
        "name_zh": "保罗·格雷厄姆",
        "temperature": 0.6,
        "categories": ["startup", "investment", "writing"],
    },
    {
        "repo": "zhang-yiming-skill",
        "tag": "zhang_yiming",
        "name": "张一鸣",
        "name_zh": "张一鸣",
        "temperature": 0.5,
        "categories": ["tech", "startup", "china"],
    },
    {
        "repo": "karpathy-skill",
        "tag": "karpathy",
        "name": "Andrej Karpathy",
        "name_zh": "安德烈·卡帕西",
        "temperature": 0.5,
        "categories": ["tech", "ai"],
    },
    {
        "repo": "ilya-sutskever-skill",
        "tag": "ilya_sutskever",
        "name": "Ilya Sutskever",
        "name_zh": "伊利亚·苏茨克维",
        "temperature": 0.4,
        "categories": ["tech", "ai"],
    },
    {
        "repo": "mrbeast-skill",
        "tag": "mrbeast",
        "name": "MrBeast",
        "name_zh": "MrBeast",
        "temperature": 0.6,
        "categories": ["content"],
    },
    {
        "repo": "trump-skill",
        "tag": "trump",
        "name": "Donald Trump",
        "name_zh": "唐纳德·特朗普",
        "temperature": 0.7,
        "categories": ["content", "persuasion"],
    },
    {
        "repo": "steve-jobs-skill",
        "tag": "steve_jobs",
        "name": "Steve Jobs",
        "name_zh": "史蒂夫·乔布斯",
        "temperature": 0.5,
        "categories": ["tech", "product"],
    },
    {
        "repo": "elon-musk-skill",
        "tag": "elon_musk",
        "name": "Elon Musk",
        "name_zh": "埃隆·马斯克",
        "temperature": 0.6,
        "categories": ["tech", "first_principles"],
    },
    {
        "repo": "munger-skill",
        "tag": "munger",
        "name": "Charlie Munger",
        "name_zh": "查理·芒格",
        "temperature": 0.4,
        "categories": ["investment", "first_principles"],
    },
    {
        "repo": "feynman-skill",
        "tag": "feynman",
        "name": "Richard Feynman",
        "name_zh": "理查德·费曼",
        "temperature": 0.5,
        "categories": ["first_principles", "science"],
    },
    {
        "repo": "naval-skill",
        "tag": "naval",
        "name": "Naval Ravikant",
        "name_zh": "纳瓦尔·拉维坎特",
        "temperature": 0.5,
        "categories": ["startup", "investment", "philosophy"],
    },
    {
        "repo": "taleb-skill",
        "tag": "taleb",
        "name": "Nassim Taleb",
        "name_zh": "纳西姆·塔勒布",
        "temperature": 0.5,
        "categories": ["investment", "first_principles", "risk"],
    },
    {
        "repo": "zhangxuefeng-skill",
        "tag": "zhangxuefeng",
        "name": "张雪峰",
        "name_zh": "张雪峰",
        "temperature": 0.6,
        "categories": ["china", "career"],
    },
    {
        "repo": "x-mentor-skill",
        "tag": "x_mentor",
        "name": "X Growth Mentor",
        "name_zh": "X增长导师",
        "temperature": 0.5,
        "categories": ["content", "growth"],
    },
]

# ── Preset definitions ────────────────────────────────────────

PRESETS: list[dict[str, Any]] = [
    {
        "preset_id": "nuwa-all-stars",
        "name": "女娲全明星顾问团",
        "description": "14位顶级思想家的认知框架全集——科技、投资、内容、第一性原理、中国视角一次集齐。适合需要多元视角碰撞的复杂决策。",
        "tags": ["nuwa", "all-stars", "advisory"],
        "filter_tags": None,  # all personas
    },
    {
        "preset_id": "nuwa-tech-titans",
        "name": "科技巨擘",
        "description": "Jobs × Musk × Karpathy × Ilya × 张一鸣——五位科技远见者的思维模型组合，适合技术战略、产品方向、AI决策。",
        "tags": ["nuwa", "tech", "ai", "product"],
        "filter_tags": ["tech"],
    },
    {
        "preset_id": "nuwa-money-minds",
        "name": "投资智囊团",
        "description": "芒格 × 塔勒布 × Naval × PG——四位投资/创业大脑的认知叠加，适合投资决策、风险评估、创业评估。",
        "tags": ["nuwa", "investment", "startup", "risk"],
        "filter_tags": ["investment"],
    },
    {
        "preset_id": "nuwa-first-principles",
        "name": "第一性原理思考团",
        "description": "费曼 × 马斯克 × 芒格 × 塔勒布——四位第一性原理大师的深度分析组合，适合拆解复杂问题、挑战假设。",
        "tags": ["nuwa", "first-principles", "analysis"],
        "filter_tags": ["first_principles"],
    },
    {
        "preset_id": "nuwa-content-empire",
        "name": "内容增长帝国",
        "description": "MrBeast × X增长导师 × Trump——内容创作、受众增长和说服力三合一，适合内容策略、社媒增长、个人品牌。",
        "tags": ["nuwa", "content", "growth", "persuasion"],
        "filter_tags": ["content"],
    },
    {
        "preset_id": "nuwa-china-guide",
        "name": "中国职场向导",
        "description": "张一鸣 × 张雪峰——创业思维与职场实战双视角，适合中国市场的职业规划、创业决策、行业选择。",
        "tags": ["nuwa", "china", "career", "startup"],
        "filter_tags": ["china"],
    },
]

# ── Section extraction ────────────────────────────────────────

# Sections to keep (in order) — these heading patterns match the nuwa SKILL.md structure
KEEP_SECTIONS = [
    "身份卡",
    "核心心智模型",
    "决策启发式",
    "表达DNA",
    "价值观与反模式",
]

# Additional sections to keep if present
OPTIONAL_SECTIONS = [
    "使用说明",
    "25种人类误判心理学",  # Munger special
]


def _extract_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Extract YAML frontmatter and return (meta, body)."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}, text
    meta_raw = m.group(1)
    meta: dict[str, str] = {}
    for line in meta_raw.split("\n"):
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def _extract_title_quote(body: str) -> str:
    """Extract the title line and opening quote if present."""
    lines = body.split("\n")
    result = []
    for line in lines[:5]:
        stripped = line.strip()
        if stripped.startswith("# ") or stripped.startswith("> "):
            result.append(stripped)
    return "\n".join(result)


def _extract_sections(body: str, section_names: list[str]) -> dict[str, str]:
    """Extract named H2 sections from markdown body."""
    # Split by H2 headers
    h2_pattern = re.compile(r"^## (.+)$", re.MULTILINE)
    splits = list(h2_pattern.finditer(body))

    sections: dict[str, str] = {}
    for i, match in enumerate(splits):
        heading = match.group(1).strip()
        start = match.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(body)
        content = body[start:end].strip()

        # Check if this heading matches any of our target sections
        for target in section_names:
            if target in heading:
                sections[target] = content
                break

    return sections


def _condense_mental_models(text: str) -> str:
    """Keep model names, one-liners, and application. Drop evidence/limitations detail."""
    lines = text.split("\n")
    result: list[str] = []
    in_evidence = False
    in_limitation = False

    for line in lines:
        stripped = line.strip()

        # Keep H3 headers (model names)
        if stripped.startswith("### "):
            in_evidence = False
            in_limitation = False
            result.append(line)
            continue

        # Keep one-liner
        if stripped.startswith("**一句话**"):
            result.append(line)
            in_evidence = False
            continue

        # Keep application
        if stripped.startswith("**应用**"):
            result.append(line)
            in_evidence = False
            in_limitation = False
            continue

        # Skip evidence blocks (verbose)
        if stripped.startswith("**证据**"):
            in_evidence = True
            continue

        # Keep limitation one-liner only
        if stripped.startswith("**局限**"):
            in_limitation = True
            # Keep just first sentence
            content = stripped.replace("**局限**：", "").replace("**局限**:", "")
            first_sentence = content.split("。")[0] + "。" if "。" in content else content.split(". ")[0] + "."
            result.append(f"**局限**：{first_sentence}")
            continue

        if in_evidence or in_limitation:
            continue

        # Keep everything else
        if stripped:
            result.append(line)

    return "\n".join(result)


def _condense_heuristics(text: str) -> str:
    """Keep heuristic names and core descriptions. Drop case studies."""
    lines = text.split("\n")
    result: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Skip case study lines
        if stripped.startswith("- 案例") or stripped.startswith("- Case"):
            continue
        if stripped:
            result.append(line)

    return "\n".join(result)


def _build_x_mentor_persona(source_dir: Path, persona_info: dict[str, Any]) -> str | None:
    """Special handler for x-mentor-skill which stores models in reference files."""
    repo = persona_info["repo"]
    skill_path = source_dir / repo / "SKILL.md"
    ref_path = source_dir / repo / "references" / "mental-models-heuristics.md"

    if not skill_path.exists():
        return None

    raw = skill_path.read_text(encoding="utf-8")
    _meta, body = _extract_frontmatter(raw)

    parts: list[str] = []

    # Title and quote
    title_quote = _extract_title_quote(body)
    if title_quote:
        parts.append(title_quote)

    parts.append(
        "\n你是一位 $10K/hr 级别的 X/Twitter 运营导师，基于 Nicolas Cole、Dickie Bush、"
        "Sahil Bloom、Justin Welsh、Dan Koe、Alex Hormozi 六位顶级创作者的方法论，"
        "精通选题策略、推文写作、Thread结构、增长引擎、算法利用和变现路径。\n"
    )

    # Extract key sections from SKILL.md
    sections = _extract_sections(body, ["导师定位", "执行规则", "通用规则"])
    if "导师定位" in sections:
        parts.append("## 导师定位\n")
        parts.append(sections["导师定位"])

    # Load models/heuristics from reference file
    if ref_path.exists():
        ref_raw = ref_path.read_text(encoding="utf-8")
        ref_sections = _extract_sections(ref_raw, ["核心心智模型", "决策启发式"])
        if "核心心智模型" in ref_sections:
            parts.append("\n## 核心心智模型\n")
            parts.append(_condense_mental_models(ref_sections["核心心智模型"]))
        if "决策启发式" in ref_sections:
            parts.append("\n## 决策启发式\n")
            parts.append(_condense_heuristics(ref_sections["决策启发式"]))

    # Execution rules (condensed — keep scenarios but not full step details)
    if "执行规则" in sections:
        parts.append("\n## 执行规则概要\n")
        # Extract just the scenario headers and core instructions
        exec_lines = sections["执行规则"].split("\n")
        condensed: list[str] = []
        for line in exec_lines:
            stripped = line.strip()
            if stripped.startswith("### ") or stripped.startswith("**") or stripped.startswith("- **"):
                condensed.append(line)
        parts.append("\n".join(condensed))

    # General rules
    if "通用规则" in sections:
        parts.append("\n## 通用规则\n")
        parts.append(sections["通用规则"])

    return "\n".join(parts)


def build_persona(source_dir: Path, persona_info: dict[str, Any]) -> str | None:
    """Read SKILL.md and build a condensed persona prompt."""
    # Special handling for x-mentor-skill
    if persona_info["repo"] == "x-mentor-skill":
        return _build_x_mentor_persona(source_dir, persona_info)

    repo = persona_info["repo"]
    skill_path = source_dir / repo / "SKILL.md"
    if not skill_path.exists():
        print(f"  ⚠️  SKILL.md not found: {skill_path}", file=sys.stderr)
        return None

    raw = skill_path.read_text(encoding="utf-8")
    _meta, body = _extract_frontmatter(raw)

    # Build persona prompt
    parts: list[str] = []

    # Title and quote
    title_quote = _extract_title_quote(body)
    if title_quote:
        parts.append(title_quote)

    # Role-play preamble
    name = persona_info["name"]
    name_zh = persona_info["name_zh"]
    display = f"{name_zh}（{name}）" if name_zh != name else name
    parts.append(f"\n你现在是 {display}。以第一人称「我」直接回应，用{display}的语气、节奏和思维方式。\n")

    # Extract key sections
    all_targets = KEEP_SECTIONS + OPTIONAL_SECTIONS
    sections = _extract_sections(body, all_targets)

    # Identity card
    if "身份卡" in sections:
        parts.append("## 身份卡\n")
        parts.append(sections["身份卡"])

    # Mental models (condensed)
    if "核心心智模型" in sections:
        parts.append("\n## 核心心智模型\n")
        parts.append(_condense_mental_models(sections["核心心智模型"]))

    # Decision heuristics (condensed)
    if "决策启发式" in sections:
        parts.append("\n## 决策启发式\n")
        parts.append(_condense_heuristics(sections["决策启发式"]))

    # Expression DNA (keep full)
    if "表达DNA" in sections:
        parts.append("\n## 表达DNA\n")
        parts.append(sections["表达DNA"])

    # Values (keep full)
    if "价值观与反模式" in sections:
        parts.append("\n## 价值观与反模式\n")
        parts.append(sections["价值观与反模式"])

    # Optional: Munger's 25 biases
    if "25种人类误判心理学" in sections:
        parts.append("\n## 25种人类误判心理学\n")
        parts.append(sections["25种人类误判心理学"])

    # Usage notes if present
    if "使用说明" in sections:
        # Insert after title, before identity
        parts.insert(2, "\n## 使用说明\n")
        parts.insert(3, sections["使用说明"])

    return "\n".join(parts)


def build_expert_entry(persona_info: dict[str, Any], persona_text: str) -> dict[str, Any]:
    """Build an oasis_experts.json entry."""
    name = persona_info["name"]
    name_zh = persona_info["name_zh"]
    display = f"{name_zh}" if name_zh != name else name
    return {
        "name": display,
        "tag": persona_info["tag"],
        "persona": persona_text,
        "temperature": persona_info["temperature"],
        "name_zh": name_zh,
        "name_en": name,
        "source": f"alchaincyf/{persona_info['repo']}",
    }


def build_workflow_yaml(
    preset_id: str, agents: list[dict[str, Any]], description: str
) -> str:
    """Generate an OASIS workflow YAML for a preset."""
    lines = [
        f"# {preset_id} — auto-generated workflow",
        f"# {description}",
        "version: 2",
        "repeat: false",
        "plan:",
        "- id: m0",
        "  manual:",
        "    author: begin",
        '    content: "请提出你的问题或议题，各位顾问将依次给出分析。"',
    ]

    for i, agent in enumerate(agents):
        tag = agent["tag"]
        name = agent.get("name_zh", agent["name"])
        lines.append(f"- id: m{i + 1}")
        lines.append(f"  expert: {tag}#oasis#{name}")

    # Synthesis node at the end
    lines.append(f"- id: m{len(agents) + 1}")
    lines.append("  manual:")
    lines.append("    author: bend")
    lines.append('    content: "所有顾问已发言完毕。"')

    # Sequential edges
    lines.append("edges:")
    for i in range(len(agents) + 1):
        lines.append(f"- - m{i}")
        lines.append(f"  - m{i + 1}")

    return "\n".join(lines) + "\n"


def build_preset(
    preset_def: dict[str, Any],
    all_personas: list[dict[str, Any]],
    persona_texts: dict[str, str],
) -> None:
    """Build a complete preset directory."""
    preset_id = preset_def["preset_id"]
    filter_tags = preset_def.get("filter_tags")

    # Select personas for this preset
    if filter_tags is None:
        selected = all_personas
    else:
        selected = [
            p for p in all_personas
            if any(t in p["categories"] for t in filter_tags)
        ]

    # Filter to those with successfully extracted text
    selected = [p for p in selected if p["tag"] in persona_texts]

    if not selected:
        print(f"  ⚠️  No personas for preset {preset_id}, skipping", file=sys.stderr)
        return

    print(f"  📦 {preset_id}: {len(selected)} personas")

    preset_dir = PRESET_ROOT / preset_id
    preset_dir.mkdir(parents=True, exist_ok=True)
    (preset_dir / "oasis" / "yaml").mkdir(parents=True, exist_ok=True)

    workflow_filename = f"{preset_id.replace('-', '_')}_advisory.yaml"

    # manifest.json
    manifest = {
        "preset_id": preset_id,
        "name": preset_def["name"],
        "description": preset_def["description"],
        "source": "alchaincyf/nuwa-skill",
        "regime": preset_id,
        "role_count": len(selected),
        "workflow_files": [workflow_filename],
        "default_team_name": preset_def["name"],
        "tags": preset_def["tags"],
    }
    (preset_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # internal_agents.json
    internal_agents = [
        {"name": p.get("name_zh", p["name"]), "tag": p["tag"]}
        for p in selected
    ]
    (preset_dir / "internal_agents.json").write_text(
        json.dumps(internal_agents, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # oasis_experts.json
    experts = [
        build_expert_entry(p, persona_texts[p["tag"]])
        for p in selected
    ]
    (preset_dir / "oasis_experts.json").write_text(
        json.dumps(experts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # source_map.json
    source_map = {
        "preset_id": preset_id,
        "roles": {
            p["tag"]: {
                "name": p.get("name_zh", p["name"]),
                "name_en": p["name"],
                "source_repo": f"alchaincyf/{p['repo']}",
                "source_file": "SKILL.md",
            }
            for p in selected
        },
    }
    (preset_dir / "source_map.json").write_text(
        json.dumps(source_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Workflow YAML
    workflow = build_workflow_yaml(preset_id, selected, preset_def["description"])
    (preset_dir / "oasis" / "yaml" / workflow_filename).write_text(
        workflow, encoding="utf-8"
    )


def main() -> None:
    source_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE

    if not source_dir.exists():
        print(f"❌ Source directory not found: {source_dir}", file=sys.stderr)
        print("   Clone repos first: see script header for instructions", file=sys.stderr)
        sys.exit(1)

    print(f"📂 Source: {source_dir}")
    print(f"📦 Target: {PRESET_ROOT}")
    print()

    # Step 1: Extract personas
    print("🔍 Extracting personas from SKILL.md files...")
    persona_texts: dict[str, str] = {}
    for p in PERSONAS:
        tag = p["tag"]
        text = build_persona(source_dir, p)
        if text:
            persona_texts[tag] = text
            char_count = len(text)
            print(f"  ✅ {p['name']:25s} → {char_count:,} chars")
        else:
            print(f"  ❌ {p['name']:25s} → FAILED")

    print(f"\n📊 Extracted {len(persona_texts)}/{len(PERSONAS)} personas")
    print()

    # Step 2: Build presets
    print("🏗️  Building presets...")
    for preset_def in PRESETS:
        build_preset(preset_def, PERSONAS, persona_texts)

    print()
    print("✅ Done! New presets:")
    for preset_def in PRESETS:
        pid = preset_def["preset_id"]
        pdir = PRESET_ROOT / pid
        if pdir.exists():
            print(f"   {pid:30s} → {pdir}")


if __name__ == "__main__":
    main()
