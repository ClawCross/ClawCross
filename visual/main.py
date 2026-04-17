"""
Visual Agent Orchestration System
==================================
A standalone frontend for visually arranging agent nodes on a 2D canvas,
then exporting the spatial layout to OASIS-compatible YAML schedule format.

Spatial Semantics:
  - Nodes connected by directed edges → sequential `expert` steps (workflow/pipeline)
  - Nodes grouped in a cluster (circle) → `parallel` step (brainstorm/group chat)
  - All nodes selected → `all_experts: true`
  - Manual injection nodes → `manual` steps

Run:
  python visual/main.py
  Open http://127.0.0.1:51210
"""

import json
import math
import os
import re
import sys
from typing import Optional

import requests
import yaml
from flask import Flask, jsonify, request, send_from_directory

# 将项目根目录添加到 sys.path，以便导入 oasis 模块
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_CURRENT_DIR)
sys.path.insert(0, _PROJECT_ROOT)

# 尝试从项目加载默认专家配置
_EXPERTS_FILE_PATH = os.path.join(_PROJECT_ROOT, "data", "prompts", "oasis_experts.json")
DEFAULT_EXPERTS_LIST = []
try:
    with open(_EXPERTS_FILE_PATH, "r", encoding="utf-8") as f:
        DEFAULT_EXPERTS_LIST = json.load(f)
except FileNotFoundError:
    # 内置默认专家配置（文件不存在时备用）
    DEFAULT_EXPERTS_LIST = [
        {"name": "创意专家", "tag": "creative", "persona": "Creative thinker", "temperature": 0.9},
        {"name": "PUA专家", "tag": "critical", "persona": "High-pressure reviewer who forces evidence, root-cause thinking, and verification", "temperature": 0.4},
        {"name": "数据分析师", "tag": "data", "persona": "Data-driven analyst", "temperature": 0.5},
        {"name": "综合顾问", "tag": "synthesis", "persona": "Synthesis advisor", "temperature": 0.5},
        {"name": "经济学家", "tag": "economist", "persona": "Economist", "temperature": 0.5},
        {"name": "法学家", "tag": "lawyer", "persona": "Legal expert", "temperature": 0.3},
        {"name": "成本限制者", "tag": "cost_controller", "persona": "Cost controller", "temperature": 0.4},
        {"name": "收益规划者", "tag": "revenue_planner", "persona": "Revenue planner", "temperature": 0.6},
        {"name": "创新企业家", "tag": "entrepreneur", "persona": "Entrepreneur", "temperature": 0.8},
        {"name": "普通人", "tag": "common_person", "persona": "Common person", "temperature": 0.7},
    ]

# 专家标签的 emoji 映射
TAG_EMOJI_MAP = {
    "creative": "🎨", "critical": "🔍", "data": "📊", "synthesis": "🎯",
    "economist": "📈", "lawyer": "⚖️", "cost_controller": "💰",
    "revenue_planner": "📊", "entrepreneur": "🚀", "common_person": "🧑",
    "manual": "📝", "custom": "⭐",
}

# ── 主 Agent 连接配置 ──
# 从 config/.env 读取 PORT_AGENT；凭证来自用户输入（不存储在后端）
_ENV_FILE_PATH = os.path.join(_PROJECT_ROOT, "config", ".env")
_DEFAULT_AGENT_PORT = "51200"
try:
    if os.path.isfile(_ENV_FILE_PATH):
        with open(_ENV_FILE_PATH, "r", encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if line.startswith("PORT_AGENT="):
                    _DEFAULT_AGENT_PORT = line.split("=", 1)[1].strip() or "51200"
except Exception:
    pass

MAIN_AGENT_URL = os.getenv("MAIN_AGENT_URL", f"http://127.0.0.1:{_DEFAULT_AGENT_PORT}")

app = Flask(__name__, static_folder=os.path.join(_CURRENT_DIR, "static"))


# ──────────────────────────────────────────────────────────────
# 空间布局 → YAML 转换逻辑
# ──────────────────────────────────────────────────────────────

def _calculate_distance(node_a: dict, node_b: dict) -> float:
    """计算两个节点之间的欧几里得距离。"""
    return math.sqrt((node_a["x"] - node_b["x"]) ** 2 + (node_a["y"] - node_b["y"]) ** 2)


def _detect_spatial_clusters(nodes: list[dict], distance_threshold: float = 150.0) -> list[list[dict]]:
    """
    使用基于距离的简单分组检测节点的空间聚类。
    距离小于 `distance_threshold` 像素的节点被视为同一聚类。
    使用并查集（Union-Find）实现高效聚类。
    """
    node_count = len(nodes)
    parent = list(range(node_count))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(idx_a, idx_b):
        root_a, root_b = find(idx_a), find(idx_b)
        if root_a != root_b:
            parent[root_a] = root_b

    # 合并距离较近的节点
    for i in range(node_count):
        for j in range(i + 1, node_count):
            if _calculate_distance(nodes[i], nodes[j]) < distance_threshold:
                union(i, j)

    # 按根节点分组
    clusters: dict[int, list[dict]] = {}
    for i in range(node_count):
        root = find(i)
        clusters.setdefault(root, []).append(nodes[i])

    return list(clusters.values())


def _is_circular_layout(nodes: list[dict], variance_tolerance: float = 0.4) -> bool:
    """
    检查节点是否大致排列成圆形。
    计算质心，然后检查到质心距离的方差是否较小。
    variance_tolerance: 判定为圆形的最大变异系数（std/mean）。
    """
    if len(nodes) < 3:
        return False

    centroid_x = sum(n["x"] for n in nodes) / len(nodes)
    centroid_y = sum(n["y"] for n in nodes) / len(nodes)

    distances = [math.sqrt((n["x"] - centroid_x) ** 2 + (n["y"] - centroid_y) ** 2) for n in nodes]
    mean_distance = sum(distances) / len(distances)
    if mean_distance < 1:
        return False

    variance = sum((d - mean_distance) ** 2 for d in distances) / len(distances)
    std_distance = math.sqrt(variance)
    coefficient_of_variation = std_distance / mean_distance

    return coefficient_of_variation < variance_tolerance


def _topological_sort_edges(edges: list[dict], node_map: dict) -> list[str]:
    """
    给定有向边，产生节点 ID 的拓扑排序。
    返回沿边方向的节点 ID 有序列表。
    """
    # 使用节点 ID 构建邻接表和入度
    adjacency: dict[str, list[str]] = {}
    in_degree: dict[str, int] = {}
    all_edge_nodes = set()

    for edge in edges:
        source_node = edge.get("source", "")
        target_node = edge.get("target", "")
        adjacency.setdefault(source_node, []).append(target_node)
        in_degree.setdefault(source_node, 0)
        in_degree[target_node] = in_degree.get(target_node, 0) + 1
        all_edge_nodes.add(source_node)
        all_edge_nodes.add(target_node)

    # Kahn 算法
    queue = [n for n in all_edge_nodes if in_degree.get(n, 0) == 0]
    sorted_result = []
    while queue:
        current_node = queue.pop(0)
        sorted_result.append(current_node)
        for neighbor in adjacency.get(current_node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return sorted_result


def _convert_node_to_yaml_name(node: dict) -> str:
    """将画布节点转换为 OASIS YAML 专家名称。

    每个节点携带一个 ``instance`` 编号（≥1），使同一代理可以多次出现
    在布局中并具有不同的身份。

    对于专家节点：
      - stateful=False（默认）→ "tag#temp#<instance>"（无状态 ExpertAgent）
      - stateful=True          → "tag#oasis#new"       （有状态 SessionExpert，自动创建）
    对于外部节点：
      - "tag#platform#<ext_id>"  （外部 API 代理，platform 如 openclaw/claude/codex）
    对于 session_agent 节点：
      - 有标签:  "tag#oasis#<agent_name>"   （标签启用 persona 查找）
      - 无标签:  "#oasis#<agent_name>"      （名称→会话查找，引擎解析）
    """
    instance = node.get("instance", 1)
    node_type = node.get("type", "expert")

    if node_type == "external":
        tag = node.get("tag", "custom")
        platform = node.get("platform", tag)  # platform from node or fallback to tag
        external_id = node.get("ext_id", "1")
        return f"{tag}#{platform}#{external_id}"

    if node_type == "session_agent":
        agent_name = node.get("agent_name") or node.get("name", "Agent")
        tag = node.get("tag", "")
        session_id = node.get("session_id", "")
        # 统一格式：所有 session 代理使用 #oasis#<name>
        # tag#oasis#name（标签启用 persona 查找）
        # #oasis#name   （无标签，仅名称→引擎的会话查找）
        if tag and tag not in ("session", ""):
            if instance > 1:
                return f"{tag}#oasis#{agent_name}#{instance}"
            return f"{tag}#oasis#{agent_name}"
        else:
            if instance > 1:
                return f"#oasis#{agent_name}#{instance}"
            return f"#oasis#{agent_name}"

    tag = node.get("tag", "custom")
    # 每节点 stateful 标志：如果设置，则使用有状态会话模式
    if node.get("stateful", False):
        return f"{tag}#oasis#new"
    return f"{tag}#temp#{instance}"


def _has_multiple_inputs(edge_list: list[dict]) -> bool:
    """检查是否有任何节点具有多个入边（扇入），需要 DAG 模式。"""
    input_count: dict[str, int] = {}
    for edge in edge_list:
        target_node = edge.get("target", "")
        input_count[target_node] = input_count.get(target_node, 0) + 1
    return any(count > 1 for count in input_count.values())


def _has_multiple_outputs(edge_list: list[dict]) -> bool:
    """检查是否有任何节点具有多个出边（扇出），需要 DAG 模式。"""
    output_count: dict[str, int] = {}
    for edge in edge_list:
        source_node = edge.get("source", "")
        output_count[source_node] = output_count.get(source_node, 0) + 1
    return any(count > 1 for count in output_count.values())


def layout_to_yaml(canvas_data: dict) -> str:
    """
    将画布布局数据转换为 OASIS 兼容的 YAML 调度格式。

    当边形成具有扇入或扇出的 DAG（节点有多个前驱或后继）时，
    输出带有 id/depends_on 的 DAG 格式，以便引擎最大化并行度。
    否则回退到更简单的线性步骤列表。

    输入数据格式：
    {
        "nodes": [
            {"id": "n1", "name": "创意专家", "tag": "creative", "x": 100, "y": 200, "type": "expert"},
            {"id": "n2", "name": "PUA专家", "tag": "critical", "x": 300, "y": 200, "type": "expert"},
            {"id": "n3", "name": "助手", "tag": "session", "x": 500, "y": 200, "type": "session_agent", "session_id": "abc123"},
            ...
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            ...
        ],
        "groups": [
            {"id": "g1", "name": "Brainstorm Group", "nodeIds": ["n3", "n4", "n5"], "type": "parallel"},
            ...
        ],
        "settings": {
            "repeat": false,
            "max_rounds": 5,
            "cluster_threshold": 150
        }
    }
    """
    nodes = canvas_data.get("nodes", [])
    edges = canvas_data.get("edges", [])
    conditional_edges_raw = canvas_data.get("conditionalEdges", [])
    selector_edges_raw = canvas_data.get("selectorEdges", [])
    groups = canvas_data.get("groups", [])
    settings = canvas_data.get("settings", {})
    has_conditional = bool(conditional_edges_raw) or canvas_data.get("hasConditional", False)
    has_selector = bool(selector_edges_raw) or canvas_data.get("hasSelector", False)
    # 始终使用版本 2 图格式（带显式边）。
    # 版本 1 仅用于向后兼容读取（scheduler.py 自动转换）。
    use_v2_format = True

    repeat_enabled = settings.get("repeat", False)
    node_lookup = {n["id"]: n for n in nodes}

    def _build_expert_step(node):
        """为 expert/external 节点构建计划步骤字典。"""
        step = {"expert": _convert_node_to_yaml_name(node)}
        # 如果节点有内容，则包含指令
        if node.get("content"):
            step["instruction"] = node["content"]
        if node.get("type") == "external":
            for config_key in ("api_url", "api_key", "model"):
                if node.get(config_key):
                    step[config_key] = node[config_key]
            if node.get("headers") and isinstance(node["headers"], dict):
                step["headers"] = node["headers"]
        return step

    def _make_special_step(node):
        node_type = node.get("type")
        if node_type == "manual":
            return {
                "manual": {
                    "author": node.get("author", "主持人"),
                    "content": node.get("content", ""),
                }
            }
        if node_type == "script":
            body = {}
            if node.get("script_command"):
                body["command"] = node["script_command"]
            if node.get("script_unix_command"):
                body["unix_command"] = node["script_unix_command"]
            if node.get("script_windows_command"):
                body["windows_command"] = node["script_windows_command"]
            if node.get("script_timeout") not in ("", None):
                body["timeout"] = node["script_timeout"]
            if node.get("script_cwd"):
                body["cwd"] = node["script_cwd"]
            return {"script": body}
        if node_type == "human":
            body = {"prompt": node.get("human_prompt", "")}
            if node.get("human_author"):
                body["author"] = node["human_author"]
            if node.get("human_reply_to") not in ("", None):
                body["reply_to"] = node["human_reply_to"]
            return {"human": body}
        return None

    def _make_plan_step(node):
        return _make_special_step(node) or _build_expert_step(node)

    plan_steps = []

    # 步骤 1：处理显式组（用户绘制的圆/聚类）
    grouped_node_ids = set()
    for group in groups:
        group_type = group.get("type", "parallel")
        member_ids = group.get("nodeIds", [])
        grouped_node_ids.update(member_ids)

        if group_type == "all":
            plan_steps.append({"all_experts": True})
        elif group_type == "parallel":
            parallel_items = []
            for node_id in member_ids:
                if node_id not in node_lookup:
                    continue
                member_node = node_lookup[node_id]
                if member_node.get("type") in ("manual", "script", "human"):
                    continue
                member_node = node_lookup[node_id]
                # 始终使用字典格式传递指令
                parallel_items.append(_build_expert_step(member_node))
            if parallel_items:
                plan_steps.append({"parallel": parallel_items})
        elif group_type == "manual":
            content = group.get("content", "Please continue the discussion.")
            author = group.get("author", "主持人")
            plan_steps.append({"manual": {"author": author, "content": content}})

    # 步骤 2：处理边 → 工作流步骤
    # 过滤连接未分组节点的边
    workflow_edges = [
        edge for edge in edges
        if edge["source"] not in grouped_node_ids and edge["target"] not in grouped_node_ids
    ]

    # 跟踪已被边/聚类消耗的节点 ID
    edge_processed_ids: set = set()

    if workflow_edges:
        # 决定：DAG 模式（id + depends_on）vs 线性模式
        requires_dag = _has_multiple_inputs(workflow_edges) or _has_multiple_outputs(workflow_edges)

        if requires_dag:
            # ── DAG 模式：发出带有 id 和 depends_on 的步骤 ──
            # 构建前驱映射：node_id → [前驱节点 ID 列表]
            predecessors: dict[str, list[str]] = {}
            all_edge_involved_nodes = set()
            for edge in workflow_edges:
                src, tgt = edge["source"], edge["target"]
                predecessors.setdefault(tgt, []).append(src)
                all_edge_involved_nodes.add(src)
                all_edge_involved_nodes.add(tgt)

            # 基于节点 ID 分配稳定的步骤 ID
            for node_id in _topological_sort_edges(workflow_edges, node_lookup):
                node = node_lookup.get(node_id)
                if not node:
                    continue
                edge_processed_ids.add(node_id)

                step = _make_plan_step(node)
                step["id"] = node_id

                dependencies = predecessors.get(node_id, [])
                if dependencies:
                    step["depends_on"] = dependencies

                plan_steps.append(step)
        else:
            # ── 线性模式（简单链）：拓扑排序 → 顺序执行 ──
            ordered_node_ids = _topological_sort_edges(workflow_edges, node_lookup)
            for node_id in ordered_node_ids:
                node = node_lookup.get(node_id)
                if not node:
                    continue
                edge_processed_ids.add(node_id)
                plan_steps.append(_make_plan_step(node))
    else:
        # 步骤 3：处理剩余的未分组、未连接节点
        # 自动检测空间模式
        remaining_nodes = [
            n for n in nodes
            if n["id"] not in grouped_node_ids and n.get("type") not in ("manual", "script", "human")
        ]

        if remaining_nodes:
            cluster_threshold = settings.get("cluster_threshold", 150)
            detected_clusters = _detect_spatial_clusters(remaining_nodes, cluster_threshold)

            for cluster in detected_clusters:
                if len(cluster) == 1:
                    # 单节点 → 顺序 expert 步骤
                    plan_steps.append(_build_expert_step(cluster[0]))
                elif _is_circular_layout(cluster):
                    # 圆形排列 → 并行（头脑风暴）
                    parallel_items = []
                    for cluster_node in cluster:
                        # 始终使用字典格式传递指令
                        parallel_items.append(_build_expert_step(cluster_node))
                    plan_steps.append({"parallel": parallel_items})
                else:
                    # 线性/分散聚类 → 按 x 坐标排序以实现从左到右顺序
                    sorted_nodes = sorted(cluster, key=lambda n: (n["x"], n["y"]))
                    for node in sorted_nodes:
                        plan_steps.append(_build_expert_step(node))

    # Step 4: Process manual injection nodes (skip those already consumed by edges)
    standalone_special_nodes = [
        n for n in nodes
        if n.get("type") in ("manual", "script", "human")
        and n["id"] not in grouped_node_ids
        and n["id"] not in edge_processed_ids
    ]
    for node in standalone_special_nodes:
        plan_steps.append(_make_plan_step(node))

    # ── 版本 2 图输出（所有布局的默认格式）──
    if use_v2_format:
        # 在版本 2 模式下，所有节点都需要 id，我们使用显式边
        v2_plan = []
        # 构建选择器节点 ID 集合
        selector_node_ids = set()
        for node in nodes:
            if node.get("isSelector", False):
                selector_node_ids.add(node["id"])
        for node in nodes:
            if node["id"] in grouped_node_ids:
                continue  # 组在别处处理
            step = {"id": node["id"]}
            if node.get("isSelector", False):
                step["selector"] = True
            step.update(_make_plan_step(node))
            v2_plan.append(step)

        # 构建固定边列表 — 排除来自选择器节点的边
        # （选择器节点的出边成为 selector_edges 选择）
        v2_fixed_edges = []
        for edge in edges:
            if edge["source"] not in selector_node_ids:
                v2_fixed_edges.append([edge["source"], edge["target"]])

        # 构建条件边列表
        v2_conditional_edges = []
        for conditional_edge in conditional_edges_raw:
            cond_entry = {
                "source": conditional_edge.get("source", ""),
                "condition": conditional_edge.get("condition", ""),
                "then": conditional_edge.get("then", ""),
            }
            if conditional_edge.get("else"):
                cond_entry["else"] = conditional_edge["else"]
            v2_conditional_edges.append(cond_entry)

        # 构建选择器边列表
        v2_selector_edges = []
        for selector_edge in selector_edges_raw:
            sel_entry = {
                "source": selector_edge.get("source", ""),
                "choices": selector_edge.get("choices", {}),
            }
            v2_selector_edges.append(sel_entry)

        schedule = {
            "version": 2,
            "repeat": repeat_enabled,
            "plan": v2_plan if v2_plan else [{"all_experts": True}],
            "edges": v2_fixed_edges,
        }
        if v2_conditional_edges:
            schedule["conditional_edges"] = v2_conditional_edges
        if v2_selector_edges:
            schedule["selector_edges"] = v2_selector_edges

        return yaml.dump(schedule, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # ── 版本 1 输出（无条件边）──
    # 构建最终 YAML 结构
    schedule = {
        "version": 1,
        "repeat": repeat_enabled,
        "plan": plan_steps if plan_steps else [{"all_experts": True}],
    }

    return yaml.dump(schedule, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ──────────────────────────────────────────────────────────────
# Flask 路由
# ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """提供主可视化编辑器页面。"""
    return send_from_directory(_CURRENT_DIR, "index.html")


@app.route("/api/experts", methods=["GET"])
def get_experts():
    """返回可用的专家池。"""
    experts_with_emoji = []
    for expert in DEFAULT_EXPERTS_LIST:
        emoji = TAG_EMOJI_MAP.get(expert["tag"], "⭐")
        experts_with_emoji.append({**expert, "emoji": emoji})
    return jsonify(experts_with_emoji)


@app.route("/api/generate-yaml", methods=["POST"])
def generate_yaml():
    """将画布布局转换为 OASIS YAML 调度格式。"""
    canvas_data = request.get_json()
    if not canvas_data:
        return jsonify({"error": "No data provided"}), 400

    try:
        yaml_output = layout_to_yaml(canvas_data)
        return jsonify({"yaml": yaml_output})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate-prompt", methods=["POST"])
def generate_prompt():
    """为 YAML 调度生成构建结构化 LLM 提示词。"""
    canvas_data = request.get_json()
    if not canvas_data:
        return jsonify({"error": "No data provided"}), 400

    try:
        prompt = _build_llm_prompt(canvas_data)
        return jsonify({"prompt": prompt})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _build_llm_prompt(canvas_data: dict) -> str:
    """从画布布局数据构建综合 LLM 提示词。"""
    nodes = canvas_data.get("nodes", [])
    edges = canvas_data.get("edges", [])
    groups = canvas_data.get("groups", [])
    settings = canvas_data.get("settings", {})

    team_name = canvas_data.get("team", "")

    node_lookup = {n["id"]: n for n in nodes}

    # ── 描述团队范围 ──
    if team_name:
        team_scope_description = f"Team: **{team_name}** — This workflow belongs to team '{team_name}'. All generated YAML and saved workflows are scoped to this team."
    else:
        team_scope_description = "Scope: **Public / Global** — This is a public workflow, not tied to any specific team."

    # ── Describe the experts involved ──
    expert_nodes = [n for n in nodes if n.get("type") not in ("manual", "script", "human")]
    manual_nodes = [n for n in nodes if n.get("type") == "manual"]
    script_nodes = [n for n in nodes if n.get("type") == "script"]
    human_nodes = [n for n in nodes if n.get("type") == "human"]

    expert_list_description = ""
    for idx, node in enumerate(expert_nodes, 1):
        instance = node.get("instance", 1)
        instance_label = f" [instance #{instance}]" if instance > 1 else ""
        if node.get("type") == "session_agent":
            expert_list_description += f"  {idx}. {node['emoji']} {node['name']}{instance_label} [SESSION AGENT: session_id={node.get('session_id', '?')}] — existing agent with its own tools & memory\n"
        elif node.get("type") == "external":
            model_info = f", model={node['model']}" if node.get("model") else ""
            expert_list_description += f"  {idx}. {node['emoji']} {node['name']}{instance_label} [EXTERNAL: tag={node.get('tag', '?')}, api_url={node.get('api_url', '?')}{model_info}] — ACP agent or external API service\n"
        else:
            stateful_label = " ⚡STATEFUL" if node.get("stateful", False) else ""
            expert_list_description += f"  {idx}. {node['emoji']} {node['name']}{instance_label}{stateful_label} (tag: {node['tag']}, temperature: {node.get('temperature', 0.5)}, source: {node.get('source', 'public')})\n"

    # ── 描述关系 ──
    relationships = []

    # 边 → 工作流连接
    if edges:
        # 过滤未分组边的 DAG 检测
        grouped_ids = set()
        for group in groups:
            grouped_ids.update(group.get("nodeIds", []))
        workflow_edges = [e for e in edges if e["source"] not in grouped_ids and e["target"] not in grouped_ids]
        is_dag = _has_multiple_inputs(workflow_edges) or _has_multiple_outputs(workflow_edges) if workflow_edges else False

        chain_descriptions = []
        for edge in edges:
            source_node = node_lookup.get(edge["source"], {})
            target_node = node_lookup.get(edge["target"], {})
            chain_descriptions.append(f"    {source_node.get('name', '?')} ({edge['source']}) → {target_node.get('name', '?')} ({edge['target']})")
        if is_dag:
            relationships.append(
                "DAG workflow connections (has fan-in or fan-out — use id/depends_on DAG format):\n"
                + "\n".join(chain_descriptions)
                + "\n    ⚠ Nodes with all predecessors complete should start immediately (maximize parallelism)."
            )
        else:
            relationships.append("Sequential workflow connections (simple chain — use linear format):\n" + "\n".join(chain_descriptions))

    # 组
    for group in groups:
        member_names = [node_lookup[nid]["name"] for nid in group.get("nodeIds", []) if nid in node_lookup]
        if group["type"] == "parallel":
            relationships.append(f"Parallel group \"{group['name']}\": [{', '.join(member_names)}] — these experts should speak simultaneously.")
        elif group["type"] == "all":
            relationships.append(f"All-experts group: all selected experts speak simultaneously.")

    # 手动注入
    if manual_nodes:
        for mn in manual_nodes:
            relationships.append(f"Manual injection by \"{mn.get('author', '主持人')}\": \"{mn.get('content', '')}\"")
    if script_nodes:
        for sn in script_nodes:
            cmd = sn.get("script_unix_command") or sn.get("script_windows_command") or sn.get("script_command", "")
            relationships.append(f"Script node \"{sn.get('name', 'Script')}\": \"{cmd}\"")
    if human_nodes:
        for hn in human_nodes:
            relationships.append(f"Human node \"{hn.get('name', 'Human')}\": prompt=\"{hn.get('human_prompt', '')}\"")

    relationships_description = "\n".join(relationships) if relationships else "  No specific relationships defined — experts are freely arranged on canvas."

    # ── 描述空间布局 ──
    spatial_description = ""
    if len(expert_nodes) >= 2:
        # 检查是否为圆形
        if _is_circular_layout(expert_nodes):
            spatial_description = "Experts are arranged in a CIRCULAR pattern, suggesting a brainstorm/round-table discussion where all speak in parallel."
        else:
            # 检查是否主要为水平（工作流式）
            x_positions = [n["x"] for n in expert_nodes]
            y_positions = [n["y"] for n in expert_nodes]
            x_range = max(x_positions) - min(x_positions)
            y_range = max(y_positions) - min(y_positions) if y_positions else 0
            if x_range > y_range * 2:
                sorted_by_x = sorted(expert_nodes, key=lambda n: n["x"])
                order_str = " → ".join(n["name"] for n in sorted_by_x)
                spatial_description = f"Experts are arranged horizontally (left to right), suggesting a SEQUENTIAL pipeline: {order_str}"
            elif y_range > x_range * 2:
                sorted_by_y = sorted(expert_nodes, key=lambda n: n["y"])
                order_str = " → ".join(n["name"] for n in sorted_by_y)
                spatial_description = f"Experts are arranged vertically (top to bottom), suggesting a SEQUENTIAL pipeline: {order_str}"
            else:
                spatial_description = "Experts are scattered on canvas — use your best judgment to determine the optimal collaboration pattern."
    elif len(expert_nodes) == 1:
        spatial_description = f"Only one expert: {expert_nodes[0]['name']}. This will be a single expert step."
    else:
        spatial_description = "No expert nodes on canvas."

    # ── 设置描述 ──
    repeat_description = "true (repeat plan every round — good for debates/discussions)" if settings.get("repeat", False) else "false (execute plan once — good for task pipelines)"
    # 描述每节点 stateful 状态
    stateful_expert_nodes = [n for n in expert_nodes if n.get("stateful", False) and n.get("type") != "external"]
    if stateful_expert_nodes:
        stateful_description = "Per-node stateful mode: " + ", ".join(f"{n['name']}(⚡stateful)" for n in stateful_expert_nodes) + " — these experts have memory & tools. Other experts are stateless."
    else:
        stateful_description = "All experts are stateless (lightweight, no memory, suitable for debates/brainstorming)"

    # ── 生成当前规则 YAML（版本 2 图格式）作为参考 ──
    try:
        current_rule_yaml = layout_to_yaml(canvas_data)
    except Exception:
        current_rule_yaml = ""

    # ── 构建最终提示词 ──
    prompt = f"""You are an OASIS schedule YAML generator. Based on the user's visual arrangement of expert agents on a canvas, generate an optimal OASIS-compatible YAML schedule.

## OASIS YAML Format Rules (Version 2 — Graph Mode)

All YAML schedules use **version: 2** with an explicit graph model: `plan` defines nodes, `edges` defines connections,
and `conditional_edges` / `selector_edges` handle branching/routing.

### Basic Graph Structure:
```yaml
version: 2
repeat: false
plan:
  - id: n1                        # Every node MUST have a unique id
    expert: "creative#temp#1"     # Stateless preset expert
  - id: n2
    expert: "critical#temp#1"
  - id: n3
    expert: "#oasis#agent_name"   # Stateful internal session agent (by name)
  - id: n4
    expert: "tag#oasis#agent_name" # Session agent with tag (tag→persona)
  - id: m1
    manual:
      author: "主持人"
      content: "Please summarize"

edges:                             # Fixed edges: always fire when source completes
  - [n1, n3]                       # n1 → n3
  - [n2, n3]                       # n2 → n3 (fan-in: n3 waits for BOTH n1 and n2)
  - [n3, n4]                       # n3 → n4
  - [n4, m1]                       # n4 → m1
```

### Conditional Branching:
```yaml
conditional_edges:
  - source: n3
    condition: "last_post_contains:APPROVED"
    then: n4                       # condition true → go to n4
    else: n2                       # condition false → loop back to n2
```

Supported conditions: `last_post_contains:<keyword>`, `last_post_not_contains:<keyword>`,
`post_count_gte:<N>`, `post_count_lt:<N>`, `always`, `!<expr>` (negate).

### Selector Routing (LLM-powered branching):
```yaml
plan:
  - id: router
    expert: "router_tag#temp#1"   # Selector can use any expert format (#temp#, #oasis#, etc.)
    selector: true                 # Mark as selector node

selector_edges:
  - source: router
    choices:
      1: branch_a                  # {{"clawcross_type": "oasis choose", "choose": 1}} → branch_a
      2: branch_b                  # {{"clawcross_type": "oasis choose", "choose": 2}} → branch_b
      3: __end__                   # {{"clawcross_type": "oasis choose", "choose": 3}} → end
```

### Parallel Groups (within plan):
```yaml
plan:
  - id: brainstorm
    parallel:
      - expert: "creative#temp#1"
      - expert: "critical#temp#1"
```

### All Experts:
```yaml
plan:
  - id: discuss
    all_experts: true              # All experts speak simultaneously
```

**Graph rules:**
- Every step MUST have a unique `id` field.
- Edges define execution order: nodes with all incoming edges satisfied run in parallel automatically.
- Nodes with no incoming edges are entry points (start immediately).
- Use `__end__` as a target to terminate the workflow.
- The graph supports cycles (via conditional/selector edges for loops).

## Expert Name Formats
1. `tag#temp#N` — Preset expert instance N (stateless, no memory), e.g. "creative#temp#1"
2. `tag#oasis#new` — Preset expert (stateful session, auto-creates new session), use when the individual node has stateful=true
3. `tag#oasis#name` — Internal session agent by name (tag enables persona lookup), e.g. "test#oasis#test1"
4. `#oasis#name` — Internal session agent by name (no tag), e.g. "#oasis#test1"
5. `tag#ext#id` — External ACP agent (tag=openclaw/codex/etc), e.g. "openclaw#ext#Alice"

## External ACP Agent — Session Number
For external ACP agents (tag = openclaw, codex, etc), the `model` field controls session:
- `model: "agent:<name>"` — session suffix defaults to **clawcrosschat** (same as group-chat ACP; shared across teams)
- `model: "agent:<name>:<session>"` — explicit suffix, e.g. separate isolation from the default
The `<name>` in model is ignored for routing (real name comes from external_agents.json `global_name`).
Session determines conversation isolation: same session = shared context, different session = separate context.

## Available Step Types (all require `id` field)
1. `expert: "Name"` — Single expert speaks
2. `parallel: [...]` — Multiple experts speak simultaneously
3. `all_experts: true` — Everyone speaks at once
4. `manual: {{author, content}}` — Inject fixed text (no LLM)
5. `selector: true` + `expert` — Selector node (LLM-powered routing, any expert format)

## Special Node: __end__
Use `__end__` as an edge target to terminate the workflow. It is not a plan node.

## Current Canvas State

### Workflow Scope:
{team_scope_description}

### Experts on canvas ({len(expert_nodes)} total):
{expert_list_description}
### Arrangement & Relationships:
{relationships_description}

### Spatial Layout Analysis:
{spatial_description}

### Settings:
- repeat: {repeat_description}
- Stateful: {stateful_description}

## Current Rule YAML (Auto-generated Reference)

**IMPORTANT**: The following YAML was auto-generated from the canvas layout using a rule-based algorithm.
It uses version 2 graph format with explicit `edges`, `conditional_edges`, and `selector_edges`.
It contains the complete configuration for each agent (including api_url, headers, model, etc.).
The auto-generated graph structure is accurate; focus on verifying the expert configurations,
edge connections, and adjusting if needed. Your output MUST also use version 2 format.

```yaml
{current_rule_yaml if current_rule_yaml else "# (no rule YAML could be generated)"}
```

## Your Task

Based on the above canvas arrangement, generate an OASIS YAML schedule that:
1. Uses **version 2** graph format (plan + edges + conditional_edges + selector_edges)
2. Respects the explicit connections (edges define execution order)
3. Respects the explicit groups (parallel groups = simultaneous speaking)
4. Interprets the spatial arrangement when no explicit connections exist
5. Uses `repeat: {str(settings.get('repeat', False)).lower()}`
6. Maximizes parallelism — nodes with no dependency relationship should be able to run concurrently

You MUST follow this exact order:
1. FIRST, call the `set_oasis_workflow` tool to save the generated YAML as a named workflow (so the workflow is immediately ready to use from the OASIS panel).
2. THEN, output the complete YAML schedule in your response text.

Both steps are mandatory and the order matters — save first, then output."""

    return prompt


@app.route("/api/agent-generate-yaml", methods=["POST"])
def agent_generate_yaml():
    """通过将 LLM 提示词发送到主代理网关来生成 YAML。

    流程：
    1. 从画布布局构建结构化 LLM 提示词
    2. 发送提示词到 mainagent /v1/chat/completions
    3. 从代理响应中提取 YAML
    4. 返回提示词和生成的 YAML
    """
    canvas_data = request.get_json()
    if not canvas_data:
        return jsonify({"error": "No data provided"}), 400

    try:
        # 步骤 1：构建提示词
        prompt = _build_llm_prompt(canvas_data)

        # 步骤 2：使用用户凭证发送到主代理
        agent_endpoint = f"{MAIN_AGENT_URL}/v1/chat/completions"

        # 从请求中提取凭证（由前端发送）
        credentials = canvas_data.get("credentials", {})
        username = credentials.get("username", "")
        password = credentials.get("password", "")

        if not username or not password:
            return jsonify({
                "prompt": prompt,
                "error": "Missing credentials. Please enter username and password in the login form.",
                "agent_yaml": None,
            })

        headers = {"Content-Type": "application/json"}
        # 使用 user:password Bearer 格式（OpenAI 兼容认证）
        headers["Authorization"] = f"Bearer {username}:{password}"

        payload = {
            "model": "webot",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a YAML schedule generator for the OASIS expert orchestration engine. "
                        "You have TWO tasks to complete IN ORDER:\n\n"
                        "1. FIRST: Call the `set_oasis_workflow` MCP tool to save the generated YAML as a named workflow "
                        "(use a descriptive name based on the task/experts, e.g. 'code_review_pipeline', 'brainstorm_trio').\n"
                        "2. THEN: Output the complete YAML schedule in your response text.\n\n"
                        "The schedule uses version 2 graph format: `plan` defines nodes (each with a unique `id`), "
                        "`edges` defines connections, and `conditional_edges`/`selector_edges` handle branching.\n"
                        "No markdown fences around the YAML. Both steps are mandatory and the order matters — save first, then output."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "stream": False,
            "session_id": "visual_orchestrator",
            "temperature": 0.3,
        }

        response = requests.post(agent_endpoint, json=payload, headers=headers, timeout=60)

        if response.status_code != 200:
            return jsonify({
                "prompt": prompt,
                "error": f"Main agent returned HTTP {response.status_code}: {response.text[:500]}",
                "agent_yaml": None,
            })

        # 步骤 3：从代理响应中提取 YAML（OpenAI 格式）
        response_data = response.json()
        agent_reply_text = ""
        try:
            agent_reply_text = response_data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            agent_reply_text = str(response_data)

        # 清理：如果存在，剥离 markdown 代码围栏
        agent_yaml = _extract_yaml_from_response(agent_reply_text)

        # 步骤 4：验证生成的 YAML
        validation_result = _validate_generated_yaml(agent_yaml)

        return jsonify({
            "prompt": prompt,
            "agent_yaml": agent_yaml,
            "agent_reply_raw": agent_reply_text,
            "validation": validation_result,
        })

    except requests.exceptions.ConnectionError:
        # 代理未运行 — 回退到仅提示词模式
        prompt = _build_llm_prompt(canvas_data)
        return jsonify({
            "prompt": prompt,
            "error": (
                f"Cannot connect to main agent at {MAIN_AGENT_URL}. "
                "Make sure mainagent.py is running (python src/mainagent.py). "
                "The prompt has been generated — you can copy it and use it manually."
            ),
            "agent_yaml": None,
        })
    except requests.exceptions.Timeout:
        prompt = _build_llm_prompt(canvas_data)
        return jsonify({
            "prompt": prompt,
            "error": "Main agent request timed out (60s). The prompt has been generated for manual use.",
            "agent_yaml": None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _extract_yaml_from_response(text: str) -> str:
    """从可能包含 markdown 围栏的 LLM 响应中提取 YAML 内容。"""
    # 尝试查找 ```yaml ... ``` 块
    yaml_match = re.search(r"```(?:yaml)?\s*\n(.*?)```", text, re.DOTALL)
    if yaml_match:
        return yaml_match.group(1).strip()

    # 尝试查找以 'version:' 开头的内容
    version_match = re.search(r"(version:\s*\d+.*)", text, re.DOTALL)
    if version_match:
        return version_match.group(1).strip()

    # 原样返回
    return text.strip()


def _validate_generated_yaml(yaml_string: str) -> dict:
    """验证生成的 YAML 并返回验证信息。"""
    try:
        parsed = yaml.safe_load(yaml_string)
        if not isinstance(parsed, dict):
            return {"valid": False, "error": "YAML did not parse to a dictionary"}

        has_version = "version" in parsed
        has_plan = "plan" in parsed
        step_count = len(parsed.get("plan", []))

        if not has_version or not has_plan:
            return {
                "valid": False,
                "error": f"Missing required fields: {'version' if not has_version else ''} {'plan' if not has_plan else ''}".strip(),
            }

        # 检查步骤类型
        step_types = []
        for step in parsed.get("plan", []):
            if isinstance(step, dict):
                if "expert" in step:
                    step_types.append("expert")
                elif "parallel" in step:
                    step_types.append("parallel")
                elif "all_experts" in step:
                    step_types.append("all_experts")
                elif "manual" in step:
                    step_types.append("manual")
                elif "script" in step:
                    step_types.append("script")
                elif "human" in step:
                    step_types.append("human")
                else:
                    step_types.append("unknown")

        return {
            "valid": True,
            "version": parsed.get("version"),
            "repeat": parsed.get("repeat", False),
            "steps": step_count,
            "step_types": step_types,
        }
    except yaml.YAMLError as e:
        return {"valid": False, "error": f"YAML parse error: {str(e)}"}


@app.route("/api/validate-yaml", methods=["POST"])
def validate_yaml():
    """验证 YAML 调度字符串。"""
    data = request.get_json()
    yaml_string = data.get("yaml", "")

    try:
        from oasis.scheduler import parse_schedule
        schedule = parse_schedule(yaml_string)
        return jsonify({
            "valid": True,
            "steps": len(schedule.steps),
            "repeat": schedule.repeat,
            "step_types": [s.step_type.value for s in schedule.steps],
        })
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)})


@app.route("/api/save-layout", methods=["POST"])
def save_layout():
    """将当前画布布局保存为 YAML（不存储单独的布局 JSON）。"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    layout_name = data.get("name", "untitled")
    safe_name = "".join(c for c in layout_name if c.isalnum() or c in "-_ ").strip() or "untitled"

    try:
        yaml_output = layout_to_yaml(data)
    except Exception as e:
        return jsonify({"error": f"YAML conversion failed: {e}"}), 500

    yaml_directory = os.path.join(_PROJECT_ROOT, "data", "user_files", "default", "oasis", "yaml")
    os.makedirs(yaml_directory, exist_ok=True)
    file_path = os.path.join(yaml_directory, f"{safe_name}.yaml")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(f"# Saved from visual orchestrator\n{yaml_output}")

    return jsonify({"saved": True, "path": file_path})


@app.route("/api/load-layouts", methods=["GET"])
def load_layouts():
    """列出所有已保存的 YAML 工作流作为可用布局。"""
    team = request.args.get("team", "").strip()
    if team:
        yaml_directory = os.path.join(_PROJECT_ROOT, "data", "user_files", "default", "teams", team, "oasis", "yaml")
    else:
        yaml_directory = os.path.join(_PROJECT_ROOT, "data", "user_files", "default", "oasis", "yaml")
    if not os.path.isdir(yaml_directory):
        return jsonify([])

    layouts = []
    for file_name in sorted(os.listdir(yaml_directory)):
        if file_name.endswith((".yaml", ".yml")):
            layouts.append(file_name.replace('.yaml', '').replace('.yml', ''))
    return jsonify(layouts)


@app.route("/api/load-layout/<name>", methods=["GET"])
def load_layout(name: str):
    """通过即时读取 YAML 文件并转换到布局来加载布局。"""
    from mcp_servers.oasis import _yaml_to_layout_data

    team = request.args.get("team", "").strip()
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_ ").strip()
    if team:
        yaml_directory = os.path.join(_PROJECT_ROOT, "data", "user_files", "default", "teams", team, "oasis", "yaml")
    else:
        yaml_directory = os.path.join(_PROJECT_ROOT, "data", "user_files", "default", "oasis", "yaml")
    file_path = os.path.join(yaml_directory, f"{safe_name}.yaml")
    if not os.path.isfile(file_path):
        file_path = os.path.join(yaml_directory, f"{safe_name}.yml")
    if not os.path.isfile(file_path):
        return jsonify({"error": "Layout not found"}), 404

    with open(file_path, "r", encoding="utf-8") as f:
        yaml_content = f.read()

    try:
        layout = _yaml_to_layout_data(yaml_content)
        layout["name"] = safe_name
        return jsonify(layout)
    except Exception as e:
        return jsonify({"error": f"YAML-to-layout conversion failed: {e}"}), 500


@app.route("/api/load-yaml-raw/<name>", methods=["GET"])
def load_yaml_raw(name: str):
    """加载工作流的原始 YAML 内容。"""
    team = request.args.get("team", "").strip()
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_ ").strip()
    if team:
        yaml_directory = os.path.join(_PROJECT_ROOT, "data", "user_files", "default", "teams", team, "oasis", "yaml")
    else:
        yaml_directory = os.path.join(_PROJECT_ROOT, "data", "user_files", "default", "oasis", "yaml")
    file_path = os.path.join(yaml_directory, f"{safe_name}.yaml")
    if not os.path.isfile(file_path):
        file_path = os.path.join(yaml_directory, f"{safe_name}.yml")
    if not os.path.isfile(file_path):
        return jsonify({"error": "YAML not found"}), 404

    with open(file_path, "r", encoding="utf-8") as f:
        yaml_content = f.read()

    return jsonify({"yaml": yaml_content, "name": safe_name})


if __name__ == "__main__":
    print("=" * 60)
    print("  🎨 Visual Agent Orchestration System")
    print("  Open http://127.0.0.1:51210 in your browser")
    print("=" * 60)
    app.run(host="0.0.0.0", port=51210, debug=True)
