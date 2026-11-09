#!/usr/bin/env python3
"""
Generate repo-owned Clawcross team presets from danghuangshang source material.

The generated assets live under:
  data/team_presets/<preset_id>/

Runtime code must depend only on the generated assets, not the external repo.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path("/tmp/clawcross-repo-compare/danghuangshang")
OUTPUT_ROOT = REPO_ROOT / "data" / "team_presets"

TANG_ROLE_MAP = {
    "zhongshu": {"name": "中书省", "file": "zhongshusheng.md"},
    "menxia": {"name": "门下省", "file": "menxiasheng.md"},
    "shangshu": {"name": "尚书省", "file": "shangshusheng.md"},
    "bingbu": {"name": "兵部", "file": "bingbu.md"},
    "hubu": {"name": "户部", "file": "hubu.md"},
    "libu": {"name": "礼部", "file": "libu.md"},
    "gongbu": {"name": "工部", "file": "gongbu.md"},
    "xingbu": {"name": "刑部", "file": "xingbu.md"},
    "libu2": {"name": "吏部", "file": "libu2.md"},
    "yushitai": {"name": "御史台", "file": "yushitai.md"},
    "shiguan": {"name": "史馆", "file": "shiguan.md"},
}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _temperature_for(tag: str) -> float:
    high_attention = {
        "silijian",
        "neige",
        "duchayuan",
        "hanlin_zhang",
        "board",
        "ceo",
        "cto",
        "cfo",
        "legal",
        "data",
        "zhongshu",
        "menxia",
        "shangshu",
        "yushitai",
    }
    return 0.4 if tag in high_attention else 0.6


def _normalize_markdown_role_name(raw: str, fallback: str) -> str:
    line = raw.splitlines()[0].strip() if raw.strip() else ""
    line = re.sub(r"^#+\s*", "", line)
    return line or fallback


def _workflow_yaml(
    preset_id: str,
    title: str,
    nodes: list[dict[str, Any]],
    edges: list[tuple[str, str]],
    *,
    conditional_edges: list[dict[str, Any]] | None = None,
    selector_edges: list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        f"# Auto-generated Clawcross preset workflow: {title}",
        "version: 2",
        "repeat: false",
        "plan:",
    ]
    for node in nodes:
        lines.append(f"- id: {node['id']}")
        if node.get("manual"):
            lines.append("  manual:")
            lines.append(f"    author: {node['manual']['author']}")
            lines.append(f"    content: {json.dumps(node['manual']['content'], ensure_ascii=False)}")
        else:
            lines.append(f"  expert: {node['expert']}")
        if node.get("selector"):
            lines.append("  selector: true")
    lines.append("edges:")
    for source, target in edges:
        lines.append(f"- - {source}")
        lines.append(f"  - {target}")
    if conditional_edges:
        lines.append("conditional_edges:")
        for edge in conditional_edges:
            lines.append(f"- source: {edge['source']}")
            lines.append(f"  condition: {json.dumps(edge['condition'], ensure_ascii=False)}")
            lines.append(f"  then: {edge['then']}")
            lines.append(f"  else: {edge['else']}")
    if selector_edges:
        lines.append("selector_edges:")
        for edge in selector_edges:
            lines.append(f"- source: {edge['source']}")
            lines.append("  choices:")
            for choice, target in edge["choices"].items():
                lines.append(f"    {choice}: {target}")
    return "\n".join(lines) + "\n"


def _write_preset(
    preset_id: str,
    *,
    display_name: str,
    description: str,
    regime: str,
    internal_agents: list[dict[str, str]],
    experts: list[dict[str, Any]],
    workflows: dict[str, str],
    source_map: dict[str, Any],
    tags: list[str],
) -> None:
    out_dir = OUTPUT_ROOT / preset_id
    oasis_yaml_dir = out_dir / "oasis" / "yaml"
    _ensure_clean_dir(out_dir)
    oasis_yaml_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "preset_id": preset_id,
        "name": display_name,
        "description": description,
        "source": "wanikua/danghuangshang",
        "regime": regime,
        "role_count": len(internal_agents),
        "workflow_files": sorted(workflows.keys()),
        "default_team_name": display_name,
        "tags": tags,
    }

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "internal_agents.json").write_text(
        json.dumps(internal_agents, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "oasis_experts.json").write_text(
        json.dumps(experts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "source_map.json").write_text(
        json.dumps(source_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for filename, contents in workflows.items():
        (oasis_yaml_dir / filename).write_text(contents, encoding="utf-8")


def build_ming() -> None:
    base = SOURCE_ROOT / "configs" / "ming-neige"
    config = _read_json(base / "openclaw.json")
    agents = config["agents"]["list"]
    internal_agents = []
    experts = []
    source_map: dict[str, Any] = {"preset_id": "ming-neige", "roles": {}}

    for agent in agents:
        tag = agent["id"]
        name = agent.get("name") or tag
        persona = str(agent.get("identity", {}).get("theme") or "").strip()
        internal_agents.append({"name": name, "tag": tag})
        experts.append(
            {
                "name": name,
                "tag": tag,
                "persona": persona,
                "temperature": _temperature_for(tag),
            }
        )
        source_map["roles"][tag] = {
            "name": name,
            "source_file": f"configs/ming-neige/openclaw.json#agents.list[{tag}]",
        }

    baseline_nodes = [
        {"id": "n0", "manual": {"author": "begin", "content": "朝会开始"}},
        {"id": "n1", "expert": "silijian#oasis#司礼监"},
        {"id": "n2", "expert": "neige#oasis#内阁"},
        {"id": "n3", "expert": "bingbu#oasis#兵部"},
        {"id": "n4", "expert": "hubu#oasis#户部"},
        {"id": "n5", "expert": "libu#oasis#礼部"},
        {"id": "n6", "expert": "gongbu#oasis#工部"},
        {"id": "n7", "expert": "libu2#oasis#吏部"},
        {"id": "n8", "expert": "xingbu#oasis#刑部"},
        {"id": "n9", "expert": "duchayuan#oasis#都察院"},
        {"id": "n10", "manual": {"author": "bend", "content": "朝会结束"}},
    ]
    baseline_edges = [
        ("n0", "n1"),
        ("n1", "n2"),
        ("n2", "n3"),
        ("n2", "n4"),
        ("n2", "n5"),
        ("n2", "n6"),
        ("n2", "n7"),
        ("n2", "n8"),
        ("n3", "n9"),
        ("n4", "n9"),
        ("n5", "n9"),
        ("n6", "n9"),
        ("n7", "n9"),
        ("n8", "n9"),
        ("n9", "n10"),
    ]
    governance_nodes = [
        {"id": "m0", "manual": {"author": "begin", "content": "圣旨下达，朝议开始"}},
        {"id": "m1", "expert": "silijian#oasis#司礼监"},
        {"id": "m2", "expert": "neige#oasis#内阁"},
        {"id": "m3", "expert": "hanlin_zhang#oasis#翰林院·掌院学士"},
        {"id": "m4", "expert": "xiuzhuan#oasis#修撰"},
        {"id": "m5", "expert": "bianxiu#oasis#编修"},
        {"id": "m6", "expert": "jiantao#oasis#检讨"},
        {"id": "m7", "expert": "shujishi#oasis#庶吉士"},
        {"id": "m8", "expert": "bingbu#oasis#兵部"},
        {"id": "m9", "expert": "gongbu#oasis#工部"},
        {"id": "m10", "expert": "hubu#oasis#户部"},
        {"id": "m11", "expert": "libu#oasis#礼部"},
        {"id": "m12", "expert": "guozijian#oasis#国子监"},
        {"id": "m13", "expert": "libu2#oasis#吏部"},
        {"id": "m14", "expert": "xingbu#oasis#刑部"},
        {"id": "m15", "expert": "neiwufu#oasis#内务府"},
        {"id": "m16", "expert": "yushanfang#oasis#御膳房"},
        {"id": "m17", "expert": "taiyiyuan#oasis#太医院"},
        {"id": "m18", "expert": "duchayuan#oasis#都察院"},
        {"id": "m19", "expert": "qijuzhu#oasis#起居注官"},
        {"id": "m20", "manual": {"author": "bend", "content": "内阁会议归档结束"}},
    ]
    governance_edges = [
        ("m0", "m1"),
        ("m1", "m2"),
        ("m2", "m3"),
        ("m2", "m8"),
        ("m2", "m10"),
        ("m2", "m11"),
        ("m2", "m13"),
        ("m2", "m14"),
        ("m2", "m15"),
        ("m3", "m4"),
        ("m4", "m5"),
        ("m5", "m6"),
        ("m6", "m7"),
        ("m7", "m19"),
        ("m8", "m9"),
        ("m9", "m18"),
        ("m10", "m17"),
        ("m17", "m18"),
        ("m11", "m12"),
        ("m12", "m18"),
        ("m13", "m18"),
        ("m14", "m18"),
        ("m15", "m16"),
        ("m16", "m18"),
        ("m18", "m19"),
        ("m19", "m20"),
    ]
    workflows = {
        "ming_neige_baseline.yaml": _workflow_yaml(
            "ming-neige",
            "明朝内阁制基线流程",
            baseline_nodes,
            baseline_edges,
        ),
        "ming_neige_governance.yaml": _workflow_yaml(
            "ming-neige",
            "明朝内阁制治理流程",
            governance_nodes,
            governance_edges,
        ),
    }
    _write_preset(
        "ming-neige",
        display_name="明朝内阁制",
        description="司礼监统筹、内阁优化、六部执行、都察院监察的高响应团队制度。",
        regime="ming-neige",
        internal_agents=internal_agents,
        experts=experts,
        workflows=workflows,
        source_map=source_map,
        tags=["imperial", "coordination", "review"],
    )


def build_modern() -> None:
    base = SOURCE_ROOT / "configs" / "modern-ceo"
    config = _read_json(base / "openclaw.json")
    agents = config["agents"]["list"]
    internal_agents = []
    experts = []
    source_map: dict[str, Any] = {"preset_id": "modern-ceo", "roles": {}}

    for agent in agents:
        tag = agent["id"]
        name = agent.get("name") or tag
        persona = str(agent.get("identity", {}).get("theme") or "").strip()
        internal_agents.append({"name": name, "tag": tag})
        experts.append(
            {
                "name": name,
                "tag": tag,
                "persona": persona,
                "temperature": _temperature_for(tag),
            }
        )
        source_map["roles"][tag] = {
            "name": name,
            "source_file": f"configs/modern-ceo/openclaw.json#agents.list[{tag}]",
        }

    baseline_nodes = [
        {"id": "m0", "manual": {"author": "begin", "content": "Board review begins"}},
        {"id": "m1", "expert": "board#oasis#Board"},
        {"id": "m2", "expert": "ceo#oasis#CEO"},
        {"id": "m3", "expert": "prod#oasis#Product"},
        {"id": "m4", "expert": "cto#oasis#CTO"},
        {"id": "m5", "expert": "eng#oasis#Engineering"},
        {"id": "m6", "expert": "qa#oasis#Quality"},
        {"id": "m7", "expert": "coo#oasis#COO"},
        {"id": "m8", "expert": "cfo#oasis#CFO"},
        {"id": "m9", "expert": "cmo#oasis#CMO"},
        {"id": "m10", "expert": "sales#oasis#Sales"},
        {"id": "m11", "expert": "cs#oasis#Customer Success"},
        {"id": "m12", "expert": "legal#oasis#Legal"},
        {"id": "m13", "expert": "data#oasis#Data"},
        {"id": "m14", "manual": {"author": "bend", "content": "Board review closed"}},
    ]
    baseline_edges = [
        ("m0", "m1"),
        ("m1", "m2"),
        ("m2", "m3"),
        ("m3", "m4"),
        ("m4", "m5"),
        ("m5", "m6"),
        ("m2", "m7"),
        ("m2", "m8"),
        ("m2", "m9"),
        ("m9", "m10"),
        ("m9", "m11"),
        ("m2", "m12"),
        ("m2", "m13"),
        ("m6", "m14"),
        ("m7", "m14"),
        ("m8", "m14"),
        ("m10", "m14"),
        ("m11", "m14"),
        ("m12", "m14"),
        ("m13", "m14"),
    ]
    operating_nodes = [
        {"id": "o0", "manual": {"author": "begin", "content": "Quarterly operating review starts"}},
        {"id": "o1", "expert": "board#oasis#Board"},
        {"id": "o2", "expert": "ceo#oasis#CEO"},
        {"id": "o3", "expert": "prod#oasis#Product"},
        {"id": "o4", "expert": "cto#oasis#CTO"},
        {"id": "o5", "expert": "eng#oasis#Engineering"},
        {"id": "o6", "expert": "qa#oasis#Quality"},
        {"id": "o7", "expert": "coo#oasis#COO"},
        {"id": "o8", "expert": "hr#oasis#Human Resources"},
        {"id": "o9", "expert": "cfo#oasis#CFO"},
        {"id": "o10", "expert": "legal#oasis#Legal"},
        {"id": "o11", "expert": "data#oasis#Data"},
        {"id": "o12", "expert": "cmo#oasis#CMO"},
        {"id": "o13", "expert": "sales#oasis#Sales"},
        {"id": "o14", "expert": "cs#oasis#Customer Success"},
        {"id": "o15", "expert": "ceo#oasis#CEO"},
        {"id": "o16", "manual": {"author": "bend", "content": "Quarterly operating review ends"}},
    ]
    operating_edges = [
        ("o0", "o1"),
        ("o1", "o2"),
        ("o2", "o3"),
        ("o2", "o7"),
        ("o2", "o9"),
        ("o2", "o12"),
        ("o3", "o4"),
        ("o4", "o5"),
        ("o5", "o6"),
        ("o6", "o15"),
        ("o7", "o8"),
        ("o8", "o15"),
        ("o9", "o10"),
        ("o10", "o11"),
        ("o11", "o15"),
        ("o12", "o13"),
        ("o13", "o14"),
        ("o14", "o15"),
        ("o15", "o16"),
    ]
    workflows = {
        "modern_ceo_baseline.yaml": _workflow_yaml(
            "modern-ceo",
            "现代企业制基线流程",
            baseline_nodes,
            baseline_edges,
        ),
        "modern_ceo_operating_model.yaml": _workflow_yaml(
            "modern-ceo",
            "现代企业制经营流",
            operating_nodes,
            operating_edges,
        ),
    }
    _write_preset(
        "modern-ceo",
        display_name="现代企业制",
        description="Board/CEO/C-level 分工明确的现代企业协作团队，适合产品、工程、运营和增长并行决策。",
        regime="modern-ceo",
        internal_agents=internal_agents,
        experts=experts,
        workflows=workflows,
        source_map=source_map,
        tags=["enterprise", "c-suite", "operations"],
    )


def build_tang() -> None:
    base = SOURCE_ROOT / "configs" / "tang-sansheng"
    soul = _read_text(base / "SOUL.md")
    internal_agents = []
    experts = []
    source_map: dict[str, Any] = {"preset_id": "tang-sansheng-beta", "roles": {}}

    for tag, meta in TANG_ROLE_MAP.items():
        md_path = base / "agents" / meta["file"]
        persona_md = _read_text(md_path)
        persona = f"{soul}\n\n---\n\n{persona_md}".strip()
        name = meta["name"] or _normalize_markdown_role_name(persona_md, tag)
        internal_agents.append({"name": name, "tag": tag})
        experts.append(
            {
                "name": name,
                "tag": tag,
                "persona": persona,
                "temperature": _temperature_for(tag),
            }
        )
        source_map["roles"][tag] = {
            "name": name,
            "source_file": f"configs/tang-sansheng/agents/{meta['file']}",
        }

    baseline_nodes = [
        {"id": "t0", "manual": {"author": "begin", "content": "朝议开启"}},
        {"id": "t1", "expert": "zhongshu#oasis#中书省"},
        {"id": "t2", "expert": "menxia#oasis#门下省"},
        {"id": "t3", "expert": "shangshu#oasis#尚书省"},
        {"id": "t4", "expert": "bingbu#oasis#兵部"},
        {"id": "t5", "expert": "hubu#oasis#户部"},
        {"id": "t6", "expert": "libu#oasis#礼部"},
        {"id": "t7", "expert": "gongbu#oasis#工部"},
        {"id": "t8", "expert": "xingbu#oasis#刑部"},
        {"id": "t9", "expert": "libu2#oasis#吏部"},
        {"id": "t10", "expert": "yushitai#oasis#御史台"},
        {"id": "t11", "expert": "shiguan#oasis#史馆"},
        {"id": "t12", "manual": {"author": "bend", "content": "朝议结束"}},
    ]
    baseline_edges = [
        ("t0", "t1"),
        ("t1", "t2"),
        ("t2", "t3"),
        ("t3", "t4"),
        ("t3", "t5"),
        ("t3", "t6"),
        ("t3", "t7"),
        ("t3", "t8"),
        ("t3", "t9"),
        ("t4", "t10"),
        ("t5", "t10"),
        ("t6", "t10"),
        ("t7", "t10"),
        ("t8", "t10"),
        ("t9", "t10"),
        ("t10", "t11"),
        ("t11", "t12"),
    ]
    review_nodes = [
        {"id": "r0", "manual": {"author": "begin", "content": "政令起草与审核开始"}},
        {"id": "r1", "expert": "zhongshu#oasis#中书省"},
        {"id": "r2", "expert": "menxia#oasis#门下省"},
        {"id": "r3", "expert": "shangshu#oasis#尚书省"},
        {"id": "r4", "expert": "bingbu#oasis#兵部"},
        {"id": "r5", "expert": "hubu#oasis#户部"},
        {"id": "r6", "expert": "libu#oasis#礼部"},
        {"id": "r7", "expert": "gongbu#oasis#工部"},
        {"id": "r8", "expert": "xingbu#oasis#刑部"},
        {"id": "r9", "expert": "libu2#oasis#吏部"},
        {"id": "r10", "expert": "yushitai#oasis#御史台"},
        {"id": "r11", "expert": "shiguan#oasis#史馆"},
        {"id": "r12", "manual": {"author": "bend", "content": "三省六部审核归档结束"}},
    ]
    review_edges = [
        ("r0", "r1"),
        ("r1", "r2"),
        ("r3", "r4"),
        ("r3", "r5"),
        ("r3", "r6"),
        ("r3", "r7"),
        ("r3", "r8"),
        ("r3", "r9"),
        ("r4", "r10"),
        ("r5", "r10"),
        ("r6", "r10"),
        ("r7", "r10"),
        ("r8", "r10"),
        ("r9", "r10"),
        ("r10", "r11"),
        ("r11", "r12"),
    ]
    review_conditionals = [
        {
            "source": "r2",
            "condition": "last_post_not_contains:通过",
            "then": "r1",
            "else": "r3",
        }
    ]
    workflows = {
        "tang_sansheng_baseline.yaml": _workflow_yaml(
            "tang-sansheng-beta",
            "唐朝三省制基线流程",
            baseline_nodes,
            baseline_edges,
        ),
        "tang_sansheng_review_loop.yaml": _workflow_yaml(
            "tang-sansheng-beta",
            "唐朝三省制审核回路",
            review_nodes,
            review_edges,
            conditional_edges=review_conditionals,
        ),
    }
    _write_preset(
        "tang-sansheng-beta",
        display_name="唐朝三省制",
        description="中书省起草、门下省审核、尚书省派发六部执行、御史台独立监察的制衡型团队制度。",
        regime="tang-sansheng",
        internal_agents=internal_agents,
        experts=experts,
        workflows=workflows,
        source_map=source_map,
        tags=["imperial", "review", "checks-and-balances"],
    )


def build_hanlin_novel() -> None:
    base = SOURCE_ROOT / "configs" / "ming-neige"
    config = _read_json(base / "openclaw.json")
    wanted = {"hanlin_zhang", "xiuzhuan", "bianxiu", "jiantao", "shujishi", "qijuzhu"}
    internal_agents = []
    experts = []
    source_map: dict[str, Any] = {"preset_id": "hanlin-novel-studio", "roles": {}}
    for agent in config["agents"]["list"]:
        tag = agent["id"]
        if tag not in wanted:
            continue
        name = agent.get("name") or tag
        persona = str(agent.get("identity", {}).get("theme") or "").strip()
        internal_agents.append({"name": name, "tag": tag})
        experts.append(
            {
                "name": name,
                "tag": tag,
                "persona": persona,
                "temperature": _temperature_for(tag),
            }
        )
        source_map["roles"][tag] = {
            "name": name,
            "source_file": f"configs/ming-neige/openclaw.json#agents.list[{tag}]",
        }

    workflows = {
        "hanlin_novel_studio.yaml": _workflow_yaml(
            "hanlin-novel-studio",
            "翰林院小说创作工作流",
            [
                {"id": "h0", "manual": {"author": "begin", "content": "小说项目立项"}},
                {"id": "h1", "expert": "hanlin_zhang#oasis#翰林院·掌院学士"},
                {"id": "h2", "expert": "xiuzhuan#oasis#修撰"},
                {"id": "h3", "expert": "bianxiu#oasis#编修"},
                {"id": "h4", "expert": "jiantao#oasis#检讨"},
                {"id": "h5", "expert": "shujishi#oasis#庶吉士"},
                {"id": "h6", "expert": "qijuzhu#oasis#起居注官"},
                {"id": "h7", "manual": {"author": "bend", "content": "小说版本归档完成"}},
            ],
            [
                ("h0", "h1"),
                ("h1", "h2"),
                ("h2", "h3"),
                ("h3", "h4"),
                ("h4", "h5"),
                ("h5", "h6"),
                ("h6", "h7"),
            ],
        )
    }
    _write_preset(
        "hanlin-novel-studio",
        display_name="翰林院小说创作局",
        description="以掌院学士、修撰、编修、检讨、庶吉士、起居注官组成的长篇创作团队预设。",
        regime="hanlin-novel",
        internal_agents=internal_agents,
        experts=experts,
        workflows=workflows,
        source_map=source_map,
        tags=["creative", "writing", "hanlin"],
    )


def main() -> None:
    if not SOURCE_ROOT.exists():
        raise SystemExit(f"Missing danghuangshang source: {SOURCE_ROOT}")
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    build_ming()
    build_tang()
    build_modern()
    build_hanlin_novel()
    print(f"Generated presets under {OUTPUT_ROOT}")


if __name__ == "__main__":
    main()
