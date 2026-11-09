#!/usr/bin/env python3
"""Build ClawCross team presets from awesome-human-distillation public-figure skills.

Reads SKILL.md files from cloned repos under /tmp/awesome-skills/,
and generates team preset directories under data/team_presets/.

Covers:
  - qiushi-skill (9 methodology sub-skills)
  - maoxuan-skill, karlmarx-skill, hu-chen-feng-skill
  - tong-jincheng-skill, fengge-wangmingtianya-perspective, zhen-ge-skill
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRESET_ROOT = PROJECT_ROOT / "data" / "team_presets"
SOURCE = Path("/tmp/awesome-skills")

# ── Helpers (reused from build_nuwa_presets) ──────────────────

def _extract_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).split("\n"):
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def _extract_sections(body: str, section_names: list[str]) -> dict[str, str]:
    h2_pattern = re.compile(r"^## (.+)$", re.MULTILINE)
    splits = list(h2_pattern.finditer(body))
    sections: dict[str, str] = {}
    for i, match in enumerate(splits):
        heading = match.group(1).strip()
        start = match.end()
        end = splits[i + 1].start() if i + 1 < len(splits) else len(body)
        content = body[start:end].strip()
        for target in section_names:
            if target in heading:
                sections[target] = content
                break
    return sections


def _condense_mental_models(text: str) -> str:
    lines = text.split("\n")
    result: list[str] = []
    in_evidence = False
    in_limitation = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            in_evidence = False
            in_limitation = False
            result.append(line)
            continue
        if stripped.startswith("**一句话**"):
            result.append(line)
            in_evidence = False
            continue
        if stripped.startswith("**应用**"):
            result.append(line)
            in_evidence = False
            in_limitation = False
            continue
        if stripped.startswith("**证据**"):
            in_evidence = True
            continue
        if stripped.startswith("**局限**"):
            in_limitation = True
            content = stripped.replace("**局限**：", "").replace("**局限**:", "")
            first = content.split("。")[0] + "。" if "。" in content else content.split(". ")[0] + "."
            result.append(f"**局限**：{first}")
            continue
        if in_evidence or in_limitation:
            continue
        if stripped:
            result.append(line)
    return "\n".join(result)


def _condense_heuristics(text: str) -> str:
    lines = text.split("\n")
    return "\n".join(l for l in lines if not l.strip().startswith("- 案例"))


def _read_skill(path: Path) -> str:
    """Read a SKILL.md and return its body (minus frontmatter)."""
    raw = path.read_text(encoding="utf-8")
    _, body = _extract_frontmatter(raw)
    return body


# ── Qiushi: read each sub-skill ──────────────────────────────

QIUSHI_SKILLS = [
    ("arming_thought",      "武装思想",     "skills/arming-thought/SKILL.md",         0.3),
    ("contradiction",       "矛盾分析法",   "skills/contradiction-analysis/SKILL.md",  0.4),
    ("practice_cognition",  "实践认识论",   "skills/practice-cognition/SKILL.md",      0.4),
    ("investigation",       "调查研究",     "skills/investigation-first/SKILL.md",     0.4),
    ("mass_line",           "群众路线",     "skills/mass-line/SKILL.md",               0.5),
    ("self_criticism",      "批评与自我批评", "skills/criticism-self-criticism/SKILL.md", 0.4),
    ("protracted",          "持久战略",     "skills/protracted-strategy/SKILL.md",     0.4),
    ("concentrate",         "集中兵力",     "skills/concentrate-forces/SKILL.md",      0.4),
    ("spark_fire",          "星火燎原",     "skills/spark-prairie-fire/SKILL.md",      0.5),
    ("overall_planning",    "统筹兼顾",     "skills/overall-planning/SKILL.md",        0.5),
]


def build_qiushi_experts(source: Path) -> list[dict[str, Any]]:
    """Build oasis_experts entries for the qiushi sub-skills."""
    repo = source / "qiushi-skill"
    experts: list[dict[str, Any]] = []

    for tag, name, rel_path, temp in QIUSHI_SKILLS:
        path = repo / rel_path
        if not path.exists():
            # Try alternate location
            alt = repo / rel_path.replace("commands/", "skills/").replace(".md", "/SKILL.md")
            if alt.exists():
                path = alt
            else:
                print(f"  ⚠️  qiushi: {rel_path} not found, skipping {name}")
                continue

        body = _read_skill(path)

        # Build persona with context
        persona = (
            f"# 求是方法论 · {name}\n\n"
            f"你是「求是」方法论体系中的「{name}」专家。"
            f"以实事求是为总原则，运用{name}的方法分析和解决问题。\n\n"
            f"{body}"
        )

        experts.append({
            "name": name,
            "tag": tag,
            "persona": persona,
            "temperature": temp,
            "name_zh": name,
            "source": "HughYau/qiushi-skill",
        })
        print(f"  ✅ {name:15s} → {len(persona):,} chars")

    return experts


# ── Other public-figure skills ────────────────────────────────

NUWA_TEMPLATE_SKILLS = [
    # These follow the nuwa SKILL.md template (身份卡, 核心心智模型, etc.)
    {
        "repo": "maoxuan-skill",
        "tag": "maoxuan",
        "name": "毛泽东",
        "name_zh": "毛泽东",
        "temperature": 0.5,
        "categories": ["strategy", "methodology"],
        "nuwa_format": True,
    },
    {
        "repo": "tong-jincheng-skill",
        "tag": "tong_jincheng",
        "name": "童锦程",
        "name_zh": "童锦程",
        "temperature": 0.6,
        "categories": ["street", "relationship"],
        "nuwa_format": True,
    },
]

RAW_SKILLS = [
    # These have unique formats — use full SKILL.md as persona
    {
        "repo": "karlmarx-skill",
        "tag": "karlmarx",
        "name": "马克思方法论",
        "name_zh": "马克思方法论",
        "temperature": 0.4,
        "categories": ["strategy", "structural"],
    },
    {
        "repo": "hu-chen-feng-skill",
        "tag": "hu_chenfeng",
        "name": "户晨风",
        "name_zh": "户晨风",
        "temperature": 0.5,
        "categories": ["street", "career"],
    },
    {
        "repo": "fengge-wangmingtianya-perspective",
        "tag": "fengge",
        "name": "峰哥",
        "name_zh": "峰哥",
        "temperature": 0.7,
        "categories": ["street", "resilience"],
    },
    {
        "repo": "zhen-ge-skill",
        "tag": "zhen_ge",
        "name": "陈震",
        "name_zh": "陈震",
        "temperature": 0.5,
        "categories": ["street", "product"],
    },
]

KEEP_SECTIONS = [
    "身份卡", "核心心智模型", "决策启发式", "表达DNA", "价值观与反模式",
    "使用说明", "内在张力",
]


def build_nuwa_format_persona(source: Path, info: dict[str, Any]) -> str | None:
    """Build persona for skills that follow nuwa SKILL.md template."""
    path = source / info["repo"] / "SKILL.md"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    _, body = _extract_frontmatter(raw)

    parts: list[str] = []

    # Title line
    for line in body.split("\n")[:5]:
        s = line.strip()
        if s.startswith("# ") or s.startswith("> "):
            parts.append(s)

    name = info["name_zh"]
    parts.append(f"\n你现在是 {name}。以第一人称「我」直接回应，用{name}的语气、节奏和思维方式。\n")

    sections = _extract_sections(body, KEEP_SECTIONS)

    if "使用说明" in sections:
        parts.append("## 使用说明\n")
        parts.append(sections["使用说明"])
    if "身份卡" in sections:
        parts.append("\n## 身份卡\n")
        parts.append(sections["身份卡"])
    if "核心心智模型" in sections:
        parts.append("\n## 核心心智模型\n")
        parts.append(_condense_mental_models(sections["核心心智模型"]))
    if "决策启发式" in sections:
        parts.append("\n## 决策启发式\n")
        parts.append(_condense_heuristics(sections["决策启发式"]))
    if "表达DNA" in sections:
        parts.append("\n## 表达DNA\n")
        parts.append(sections["表达DNA"])
    if "价值观与反模式" in sections:
        parts.append("\n## 价值观与反模式\n")
        parts.append(sections["价值观与反模式"])
    if "内在张力" in sections:
        parts.append("\n## 内在张力\n")
        parts.append(sections["内在张力"])

    return "\n".join(parts)


def build_raw_persona(source: Path, info: dict[str, Any]) -> str | None:
    """Build persona from raw SKILL.md (non-nuwa format)."""
    path = source / info["repo"] / "SKILL.md"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8")
    _, body = _extract_frontmatter(raw)

    name = info["name_zh"]
    preamble = f"你现在是 {name}。以{name}的视角和方法论分析问题。\n\n"
    return preamble + body


def build_other_experts(source: Path) -> dict[str, dict[str, Any]]:
    """Build expert entries for non-qiushi public figures. Returns {tag: expert_dict}."""
    experts: dict[str, dict[str, Any]] = {}

    for info in NUWA_TEMPLATE_SKILLS:
        text = build_nuwa_format_persona(source, info)
        if text:
            experts[info["tag"]] = {
                "name": info["name_zh"],
                "tag": info["tag"],
                "persona": text,
                "temperature": info["temperature"],
                "name_zh": info["name_zh"],
                "source": f"awesome-human-distillation/{info['repo']}",
                "categories": info["categories"],
            }
            print(f"  ✅ {info['name']:15s} → {len(text):,} chars (nuwa format)")

    for info in RAW_SKILLS:
        text = build_raw_persona(source, info)
        if text:
            experts[info["tag"]] = {
                "name": info["name_zh"],
                "tag": info["tag"],
                "persona": text,
                "temperature": info["temperature"],
                "name_zh": info["name_zh"],
                "source": f"awesome-human-distillation/{info['repo']}",
                "categories": info["categories"],
            }
            print(f"  ✅ {info['name']:15s} → {len(text):,} chars (raw format)")

    return experts


# ── Workflow builder ──────────────────────────────────────────

def build_workflow_yaml(preset_id: str, agents: list[dict], desc: str) -> str:
    lines = [
        f"# {preset_id} — auto-generated workflow",
        f"# {desc}",
        "version: 2",
        "repeat: false",
        "plan:",
        "- id: m0",
        "  manual:",
        "    author: begin",
        '    content: "请提出你的问题或议题，各位专家将依次给出分析。"',
    ]
    for i, a in enumerate(agents):
        lines.append(f"- id: m{i+1}")
        lines.append(f"  expert: {a['tag']}#oasis#{a['name']}")
    lines.append(f"- id: m{len(agents)+1}")
    lines.append("  manual:")
    lines.append("    author: bend")
    lines.append('    content: "所有专家已发言完毕。"')
    lines.append("edges:")
    for i in range(len(agents)+1):
        lines.append(f"- - m{i}")
        lines.append(f"  - m{i+1}")
    return "\n".join(lines) + "\n"


# ── Preset builder ────────────────────────────────────────────

def write_preset(
    preset_id: str,
    name: str,
    description: str,
    tags: list[str],
    experts: list[dict[str, Any]],
    source: str,
) -> None:
    d = PRESET_ROOT / preset_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "oasis" / "yaml").mkdir(parents=True, exist_ok=True)

    wf_name = f"{preset_id.replace('-','_')}_advisory.yaml"

    # manifest
    manifest = {
        "preset_id": preset_id,
        "name": name,
        "description": description,
        "source": source,
        "regime": preset_id,
        "role_count": len(experts),
        "workflow_files": [wf_name],
        "default_team_name": name,
        "tags": tags,
    }
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # internal_agents
    agents = [{"name": e["name"], "tag": e["tag"]} for e in experts]
    (d / "internal_agents.json").write_text(json.dumps(agents, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # oasis_experts
    clean_experts = [
        {k: v for k, v in e.items() if k != "categories"}
        for e in experts
    ]
    (d / "oasis_experts.json").write_text(json.dumps(clean_experts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # source_map
    sm = {
        "preset_id": preset_id,
        "roles": {
            e["tag"]: {"name": e["name"], "source_repo": e.get("source", "")}
            for e in experts
        },
    }
    (d / "source_map.json").write_text(json.dumps(sm, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # workflow
    wf = build_workflow_yaml(preset_id, experts, description)
    (d / "oasis" / "yaml" / wf_name).write_text(wf, encoding="utf-8")

    print(f"  📦 {preset_id}: {len(experts)} experts")


# ── Main ──────────────────────────────────────────────────────

def main() -> None:
    if not SOURCE.exists():
        print(f"❌ Source not found: {SOURCE}", file=sys.stderr)
        sys.exit(1)

    print("🔍 Building qiushi experts...")
    qiushi_experts = build_qiushi_experts(SOURCE)

    print("\n🔍 Building other public-figure experts...")
    other = build_other_experts(SOURCE)

    print("\n🏗️  Writing presets...")

    # 1. Qiushi methodology team
    write_preset(
        "nuwa-qiushi",
        "求是方法论智囊团",
        "教员思想九大方法论工具——矛盾分析、调查研究、群众路线、持久战略、集中兵力等，系统性武装AI的问题分析和解决能力。",
        ["nuwa", "qiushi", "methodology", "strategy"],
        qiushi_experts,
        "HughYau/qiushi-skill",
    )

    # 2. Strategic analysts
    strat_tags = ["maoxuan", "karlmarx"]
    strat_experts = [other[t] for t in strat_tags if t in other]
    if strat_experts:
        write_preset(
            "nuwa-strategists",
            "战略分析团",
            "毛泽东 × 马克思方法论——战略竞争分析、权力结构解读、系统性问题诊断的双重视角。",
            ["nuwa", "strategy", "structural", "methodology"],
            strat_experts,
            "awesome-human-distillation",
        )

    # 3. Street wisdom / reality check
    street_tags = ["hu_chenfeng", "tong_jincheng", "fengge", "zhen_ge"]
    street_experts = [other[t] for t in street_tags if t in other]
    if street_experts:
        write_preset(
            "nuwa-street-wisdom",
            "江湖智慧团",
            "户晨风 × 童锦程 × 峰哥 × 陈震——接地气的现实主义决策视角，适合职业选择、人生决策、产品评审。",
            ["nuwa", "street-wisdom", "career", "reality-check"],
            street_experts,
            "awesome-human-distillation",
        )

    # 4. All awesome public figures combined
    all_awesome = list(other.values())
    if all_awesome:
        write_preset(
            "nuwa-awesome-figures",
            "公众人物全集",
            "毛泽东 × 马克思 × 户晨风 × 童锦程 × 峰哥 × 陈震——awesome-human-distillation 公众人物思维框架全集。",
            ["nuwa", "awesome", "public-figures"],
            all_awesome,
            "awesome-human-distillation",
        )

    print("\n✅ Done!")


if __name__ == "__main__":
    main()
