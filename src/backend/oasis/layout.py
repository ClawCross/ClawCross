"""A workflow YAML as the visual editor's layout: nodes, edges, groups."""

from __future__ import annotations

import json
import os

import yaml as _yaml

from common.runtime_paths import PROJECT_ROOT

# ======================================================================
# YAML → Layout conversion helpers
# ======================================================================

# Tag → display info mapping (same as src/frontend/visual.py)
_TAG_EMOJI = {
    "creative": "🎨", "critical": "🔍", "data": "📊", "synthesis": "🎯",
    "economist": "📈", "lawyer": "⚖️", "cost_controller": "💰",
    "revenue_planner": "📊", "entrepreneur": "🚀", "common_person": "🧑",
    "manual": "📝", "custom": "⭐",
}
_TAG_NAMES: dict[str, str] = {}

# Try to load names from preset experts JSON
_EXPERTS_JSON = os.path.join(str(PROJECT_ROOT), "data", "prompts", "oasis_experts.json")
try:
    with open(_EXPERTS_JSON, "r", encoding="utf-8") as _ef:
        for _exp in json.load(_ef):
            _TAG_NAMES[_exp["tag"]] = _exp["name"]
except Exception:
    pass

def _participant_node(step: dict) -> dict | None:
    """The canvas node for a plan item naming a participant, or None.

    ``agent: <ref>`` → an ``agent`` node; ``persona: <tag>`` → a ``persona`` node
    carrying its ``instance`` and ``tools`` (none / all / [names]).
    """
    if "agent" in step:
        ref = str(step["agent"] or "").strip()
        return {"type": "agent", "agent": ref, "tag": "", "name": ref, "emoji": "🤖",
                "temperature": 0.5, "instance": 1, "session_id": ""}
    if "persona" in step:
        tag = str(step["persona"] or "").strip()
        tools = step.get("tools", "none")
        return {"type": "persona", "tag": tag, "name": _TAG_NAMES.get(tag, tag), "emoji": _TAG_EMOJI.get(tag, "⭐"),
                "temperature": 0.5, "instance": int(step.get("instance", 1)), "session_id": "",
                "tools": tools if tools not in (None, False, "", []) else "none"}
    return None

def yaml_to_layout(yaml_str: str) -> dict:
    """Convert OASIS YAML schedule string to visual layout JSON.

    Pure deterministic transformation — no LLM needed.
    Nodes are auto-positioned left-to-right (sequential) / top-to-bottom (parallel).
    Supports DAG mode: steps with ``id`` and ``depends_on`` fields are laid out
    using topological-level positioning (independent branches in parallel columns).
    Supports Version 2 graph mode: explicit edges, conditional_edges, selector_edges.
    """
    data = _yaml.safe_load(yaml_str)
    if not isinstance(data, dict) or "plan" not in data:
        raise ValueError("YAML must contain 'plan' key")

    plan = data.get("plan", [])
    repeat = data.get("repeat", True)
    version = data.get("version", 1)

    # Version 2: explicit graph with edges / conditional_edges / selector_edges
    if version >= 2:
        return _yaml_v2_to_layout(data)

    # Detect DAG mode: any step has an 'id' field
    is_dag = any(isinstance(s, dict) and "id" in s for s in plan)

    if is_dag:
        return _yaml_dag_to_layout(plan, repeat)
    else:
        return _yaml_linear_to_layout(plan, repeat)

def _yaml_v2_to_layout(data: dict) -> dict:
    """Convert Version 2 graph YAML to canvas layout JSON.

    Handles explicit edges, conditional_edges, selector_edges, and selector nodes.
    Nodes are positioned using topological-level layout based on the edges list.
    """
    plan = data.get("plan", [])
    repeat = data.get("repeat", False)
    raw_edges = data.get("edges", [])
    raw_cond_edges = data.get("conditional_edges", [])
    raw_sel_edges = data.get("selector_edges", [])

    nodes: list[dict] = []
    edges: list[dict] = []

    nid = 1
    eid = 1

    # ── Layout constants ──
    MARGIN_X = 60
    MARGIN_Y = 40
    GAP_X = 260
    GAP_Y = 90

    # Build set of selector node step_ids
    selector_step_ids = set()
    for se in raw_sel_edges:
        src = se.get("source", "")
        if src:
            selector_step_ids.add(src)

    # First pass: create nodes, build step_id → node_id mapping
    step_id_to_node_id: dict[str, str] = {}
    step_ids_ordered: list[str] = []

    for step in plan:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id", ""))
        node_id = f"on{nid}"; nid += 1

        if (info := _participant_node(step)) is not None:
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                **info,
                "author": "主持人",
                "content": step.get("instruction", ""),
                "source": "",
            }
            # Mark selector nodes
            if step.get("selector") or step_id in selector_step_ids:
                node["isSelector"] = True
                node["emoji"] = "🎯"
        elif "manual" in step:
            manual = step["manual"]
            _author = manual.get("author", "主持人") if isinstance(manual, dict) else "主持人"
            _content = manual.get("content", "") if isinstance(manual, dict) else ""
            # Detect start / end special manual nodes by author
            if _author in ("begin", "bstart"):
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "开始", "emoji": "🚀",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "begin",
                    "content": _content,
                    "source": "",
                }
            elif _author == "bend":
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "结束", "emoji": "🏁",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "bend",
                    "content": _content,
                    "source": "",
                }
            else:
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "手动注入", "emoji": "📝",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": _author,
                    "content": _content,
                    "source": "",
                }
        elif "script" in step:
            script = step["script"]
            if isinstance(script, dict):
                command = script.get("command", "")
                unix_command = script.get("unix_command", "")
                windows_command = script.get("windows_command", "")
                timeout = script.get("timeout", "")
                cwd = script.get("cwd", "")
            else:
                command = str(script or "")
                unix_command = ""
                windows_command = ""
                timeout = ""
                cwd = ""
            preview = unix_command or windows_command or command
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "script", "tag": "script",
                "name": "脚本节点", "emoji": "🧪",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": "script",
                "content": preview,
                "source": "",
                "script_command": command,
                "script_unix_command": unix_command,
                "script_windows_command": windows_command,
                "script_timeout": timeout,
                "script_cwd": cwd,
            }
        elif "human" in step:
            human = step["human"]
            if isinstance(human, dict):
                prompt = human.get("prompt", "")
                author = human.get("author", "主持人")
                reply_to = human.get("reply_to", "")
            else:
                prompt = str(human or "")
                author = "主持人"
                reply_to = ""
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "human", "tag": "human",
                "name": "人类节点", "emoji": "🙋",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": author,
                "content": prompt,
                "source": "",
                "human_prompt": prompt,
                "human_author": author,
                "human_reply_to": reply_to,
            }
        elif "all_experts" in step:
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "expert", "tag": "all",
                "name": "全员讨论", "emoji": "👥",
                "temperature": 0.5, "instance": 1, "session_id": "",
                "author": "主持人", "content": "", "source": "",
            }
        else:
            continue

        nodes.append(node)
        if step_id:
            step_id_to_node_id[step_id] = node_id
            step_ids_ordered.append(step_id)

    # Build edges from explicit edges list
    for e in raw_edges:
        if isinstance(e, list) and len(e) >= 2:
            src_nid = step_id_to_node_id.get(str(e[0]))
            tgt_nid = step_id_to_node_id.get(str(e[1]))
        elif isinstance(e, dict):
            src_nid = step_id_to_node_id.get(str(e.get("source", "")))
            tgt_nid = step_id_to_node_id.get(str(e.get("target", "")))
        else:
            continue
        if src_nid and tgt_nid:
            edges.append({"id": f"oe{eid}", "source": src_nid, "target": tgt_nid})
            eid += 1

    # ── Topological layout using edges (longest path) ──
    # Build predecessor map from edges
    preds: dict[str, list[str]] = {sid: [] for sid in step_ids_ordered}
    for e in raw_edges:
        if isinstance(e, list) and len(e) >= 2:
            src_sid, tgt_sid = str(e[0]), str(e[1])
        elif isinstance(e, dict):
            src_sid = str(e.get("source", ""))
            tgt_sid = str(e.get("target", ""))
        else:
            continue
        if tgt_sid in preds and src_sid in step_id_to_node_id:
            preds[tgt_sid].append(src_sid)

    # NOTE: conditional_edges and selector_edges are NOT added to preds
    # because they may form cycles (e.g. else-branch loops back),
    # which would cause infinite recursion in the topological sort.
    # Only fixed edges (which form a DAG) are used for layer computation.

    layer: dict[str, int] = {}
    _visiting: set[str] = set()  # cycle guard
    def _get_layer(sid: str) -> int:
        if sid in layer:
            return layer[sid]
        if sid in _visiting:
            # Cycle detected — break it by treating as root
            layer[sid] = 0
            return 0
        _visiting.add(sid)
        deps = preds.get(sid, [])
        if not deps:
            layer[sid] = 0
        else:
            layer[sid] = max(_get_layer(d) for d in deps) + 1
        _visiting.discard(sid)
        return layer[sid]

    for sid in step_ids_ordered:
        _get_layer(sid)

    # Group by layer
    layers: dict[int, list[tuple[str, dict]]] = {}
    for sid in step_ids_ordered:
        lv = layer.get(sid, 0)
        nd = next((n for n in nodes if n["id"] == step_id_to_node_id.get(sid)), None)
        if nd:
            layers.setdefault(lv, []).append((sid, nd))

    # Barycenter ordering
    node_y: dict[str, float] = {}
    for lv in sorted(layers.keys()):
        layer_items = layers[lv]
        if lv > 0:
            def _bary(sid: str) -> float:
                deps = preds.get(sid, [])
                ys = [node_y[d] for d in deps if d in node_y]
                return sum(ys) / len(ys) if ys else 0.0
            layer_items.sort(key=lambda t: _bary(t[0]))
            layers[lv] = layer_items
        count = len(layer_items)
        total_h = (count - 1) * GAP_Y
        y_start = MARGIN_Y + max(0, (400 - total_h) // 2)
        for i, (sid, _nd) in enumerate(layer_items):
            y = y_start + i * GAP_Y
            node_y[sid] = y

    # Assign final x, y coordinates
    for lv, layer_items in sorted(layers.items()):
        x = MARGIN_X + lv * GAP_X
        for sid, nd in layer_items:
            nd["x"] = x
            nd["y"] = int(node_y.get(sid, MARGIN_Y))

    # Build conditional edges output for frontend
    cond_edges_out = []
    for ce in raw_cond_edges:
        src_nid = step_id_to_node_id.get(str(ce.get("source", "")))
        then_nid = step_id_to_node_id.get(str(ce.get("then", "")))
        else_nid = step_id_to_node_id.get(str(ce.get("else", ""))) if ce.get("else") else ""
        if src_nid and then_nid:
            cond_edges_out.append({
                "source": src_nid,
                "condition": ce.get("condition", ""),
                "then": then_nid,
                "else": else_nid or "",
            })

    # Build selector edges output for frontend
    sel_edges_out = []
    for se in raw_sel_edges:
        src_nid = step_id_to_node_id.get(str(se.get("source", "")))
        choices = se.get("choices", {})
        if src_nid and choices:
            mapped_choices = {}
            for num, tgt_sid in choices.items():
                tgt_nid = step_id_to_node_id.get(str(tgt_sid))
                if tgt_nid:
                    mapped_choices[int(num)] = tgt_nid
            if mapped_choices:
                sel_edges_out.append({"source": src_nid, "choices": mapped_choices})

    layout = {
        "nodes": nodes,
        "edges": edges,
        "conditionalEdges": cond_edges_out,
        "selectorEdges": sel_edges_out,
        "groups": [],
        "settings": {
            "repeat": repeat,
            "max_rounds": 5,
            "cluster_threshold": 150,
        },
    }
    return layout

def _yaml_dag_to_layout(plan: list, repeat: bool) -> dict:
    """Convert DAG-mode plan (steps with id/depends_on) to canvas layout.

    Layout strategy (optimised):
    - Nodes are assigned to layers via longest-path from roots.
    - Horizontal gap adapts to graph width so the canvas stays readable.
    - Within each layer, nodes are sorted by the median y-position of their
      predecessors (barycenter heuristic) to minimise edge crossings.
    - All y-coordinates are guaranteed ≥ margin (no negative positions).
    """
    nodes: list[dict] = []
    edges: list[dict] = []

    nid = 1
    eid = 1

    # ── Layout constants ──
    NODE_W = 160          # approximate rendered width of a canvas-node
    MARGIN_X = 60         # left margin
    MARGIN_Y = 40         # top margin
    GAP_X = 260           # horizontal gap between layers (> NODE_W + breathing room)
    GAP_Y = 90            # vertical gap between nodes in the same layer

    # First pass: create nodes, build step_id → node_id mapping
    step_id_to_node_id: dict[str, str] = {}
    step_items: list[tuple[str, dict, list[str]]] = []  # (step_id, node_dict, depends_on)

    for step in plan:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id", ""))
        depends_on = step.get("depends_on", [])
        if isinstance(depends_on, str):
            depends_on = [depends_on]

        node_id = f"on{nid}"; nid += 1

        if (info := _participant_node(step)) is not None:
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                **info,
                "author": "主持人",
                "content": step.get("instruction", ""),
                "source": "",
            }
        elif "manual" in step:
            manual = step["manual"]
            _author = manual.get("author", "主持人") if isinstance(manual, dict) else "主持人"
            _content = manual.get("content", "") if isinstance(manual, dict) else ""
            if _author in ("begin", "bstart"):
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "开始", "emoji": "🚀",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "begin",
                    "content": _content,
                    "source": "",
                }
            elif _author == "bend":
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "结束", "emoji": "🏁",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "bend",
                    "content": _content,
                    "source": "",
                }
            else:
                node = {
                    "id": node_id,
                    "x": 0, "y": 0,
                    "type": "manual", "tag": "manual",
                    "name": "手动注入", "emoji": "📝",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": _author,
                    "content": _content,
                    "source": "",
                }
        elif "script" in step:
            script = step["script"]
            if isinstance(script, dict):
                command = script.get("command", "")
                unix_command = script.get("unix_command", "")
                windows_command = script.get("windows_command", "")
                timeout = script.get("timeout", "")
                cwd = script.get("cwd", "")
            else:
                command = str(script or "")
                unix_command = ""
                windows_command = ""
                timeout = ""
                cwd = ""
            preview = unix_command or windows_command or command
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "script", "tag": "script",
                "name": "脚本节点", "emoji": "🧪",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": "script",
                "content": preview,
                "source": "",
                "script_command": command,
                "script_unix_command": unix_command,
                "script_windows_command": windows_command,
                "script_timeout": timeout,
                "script_cwd": cwd,
            }
        elif "human" in step:
            human = step["human"]
            if isinstance(human, dict):
                prompt = human.get("prompt", "")
                author = human.get("author", "主持人")
                reply_to = human.get("reply_to", "")
            else:
                prompt = str(human or "")
                author = "主持人"
                reply_to = ""
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "human", "tag": "human",
                "name": "人类节点", "emoji": "🙋",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": author,
                "content": prompt,
                "source": "",
                "human_prompt": prompt,
                "human_author": author,
                "human_reply_to": reply_to,
            }
        elif "all_experts" in step:
            node = {
                "id": node_id,
                "x": 0, "y": 0,
                "type": "expert", "tag": "all",
                "name": "全员讨论", "emoji": "👥",
                "temperature": 0.5, "instance": 1, "session_id": "",
                "author": "主持人", "content": "", "source": "",
            }
        else:
            continue

        nodes.append(node)
        if step_id:
            step_id_to_node_id[step_id] = node_id
        step_items.append((step_id, node, depends_on))

    # Build edges from depends_on
    for step_id, node, depends_on in step_items:
        node_id = node["id"]
        for dep in depends_on:
            src_node_id = step_id_to_node_id.get(dep)
            if src_node_id:
                edges.append({"id": f"oe{eid}", "source": src_node_id, "target": node_id})
                eid += 1

    # ── Compute topological layer (longest path from roots) ──
    preds: dict[str, list[str]] = {}
    for step_id, _node, depends_on in step_items:
        preds[step_id] = [d for d in depends_on if d in step_id_to_node_id]

    layer: dict[str, int] = {}
    def _get_layer(sid: str) -> int:
        if sid in layer:
            return layer[sid]
        deps = preds.get(sid, [])
        if not deps:
            layer[sid] = 0
            return 0
        lv = max(_get_layer(d) for d in deps) + 1
        layer[sid] = lv
        return lv

    for step_id, _node, _deps in step_items:
        if step_id:
            _get_layer(step_id)

    # ── Group by layer ──
    layers: dict[int, list[tuple[str, dict]]] = {}
    for step_id, node, _deps in step_items:
        lv = layer.get(step_id, 0)
        layers.setdefault(lv, []).append((step_id, node))

    # ── Barycenter ordering to reduce edge crossings ──
    # For layer 0, keep original YAML order.
    # For subsequent layers, sort nodes by the median y-position of predecessors.
    node_y: dict[str, float] = {}  # step_id → assigned y

    for lv in sorted(layers.keys()):
        layer_items = layers[lv]

        if lv > 0:
            # Compute barycenter for each node
            def _bary(sid: str) -> float:
                deps = preds.get(sid, [])
                ys = [node_y[d] for d in deps if d in node_y]
                return sum(ys) / len(ys) if ys else 0.0
            layer_items.sort(key=lambda t: _bary(t[0]))
            layers[lv] = layer_items

        # Assign y positions — centre the layer vertically
        count = len(layer_items)
        total_h = (count - 1) * GAP_Y
        y_start = MARGIN_Y + max(0, (400 - total_h) // 2)  # aim for ~400px canvas height centre
        for i, (sid, _node) in enumerate(layer_items):
            y = y_start + i * GAP_Y
            node_y[sid] = y

    # ── Assign final x, y coordinates ──
    for lv, layer_items in sorted(layers.items()):
        x = MARGIN_X + lv * GAP_X
        for sid, node in layer_items:
            node["x"] = x
            node["y"] = int(node_y.get(sid, MARGIN_Y))

    layout = {
        "nodes": nodes,
        "edges": edges,
        "groups": [],
        "settings": {
            "repeat": repeat,
            "max_rounds": 5,
            "cluster_threshold": 150,
        },
    }
    return layout

def _yaml_linear_to_layout(plan: list, repeat: bool) -> dict:
    """Convert linear plan (no id/depends_on) to canvas layout.

    Optimised layout:
    - Wider horizontal spacing so nodes don't overlap.
    - Parallel groups: fan-out edges from prev → every member, fan-in edges
      from every member → next step (instead of only first/last member).
    - Vertical centering of parallel members around the baseline.
    - Group boxes with proper padding.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    groups: list[dict] = []

    nid = 1
    eid = 1
    gid = 1

    # ── Layout constants ──
    MARGIN_X = 60
    BASE_Y = 240           # vertical baseline (enough headroom for parallel groups)
    GAP_X = 260            # horizontal gap between steps
    GAP_Y_PARALLEL = 90    # vertical gap between parallel members
    GROUP_PAD = 30         # padding around group box

    cursor_x = MARGIN_X
    prev_node_ids: list[str] = []  # may be multiple for fan-in after parallel group

    for step in plan:
        if not isinstance(step, dict):
            continue

        # --- expert step ---
        if (info := _participant_node(step)) is not None:
            node_id = f"on{nid}"; nid += 1
            node = {
                "id": node_id,
                "x": cursor_x,
                "y": BASE_Y,
                **info,
                "author": "主持人",
                "content": step.get("instruction", ""),
                "source": "",
            }
            nodes.append(node)
            for pid in prev_node_ids:
                edges.append({"id": f"oe{eid}", "source": pid, "target": node_id})
                eid += 1
            prev_node_ids = [node_id]
            cursor_x += GAP_X

        # --- parallel step ---
        elif "parallel" in step:
            members = step["parallel"]
            if not isinstance(members, list):
                continue
            group_node_ids: list[str] = []
            group_x = cursor_x
            count = len(members)
            total_h = (count - 1) * GAP_Y_PARALLEL
            y_start = BASE_Y - total_h // 2  # centre around baseline

            for idx, item in enumerate(members):
                info = _participant_node(item) if isinstance(item, dict) else None
                if info is None:
                    continue
                instruction = item.get("instruction", "")
                node_id = f"on{nid}"; nid += 1
                node = {
                    "id": node_id,
                    "x": group_x,
                    "y": y_start + idx * GAP_Y_PARALLEL,
                    **info,
                    "author": "主持人",
                    "content": instruction,
                    "source": "",
                }
                nodes.append(node)
                group_node_ids.append(node_id)

            # Create group container
            if group_node_ids:
                g_nodes = [n for n in nodes if n["id"] in group_node_ids]
                min_x = min(n["x"] for n in g_nodes) - GROUP_PAD
                min_y = min(n["y"] for n in g_nodes) - GROUP_PAD
                max_x = max(n["x"] for n in g_nodes) + 160 + GROUP_PAD
                max_y = max(n["y"] for n in g_nodes) + 50 + GROUP_PAD
                groups.append({
                    "id": f"og{gid}",
                    "name": "🔀 并行",
                    "type": "parallel",
                    "x": min_x,
                    "y": min_y,
                    "w": max_x - min_x,
                    "h": max_y - min_y,
                    "nodeIds": group_node_ids,
                })
                gid += 1

                # Fan-out: prev → every member
                for pid in prev_node_ids:
                    for mid in group_node_ids:
                        edges.append({"id": f"oe{eid}", "source": pid, "target": mid})
                        eid += 1
                # All members become prev (fan-in into next step)
                prev_node_ids = list(group_node_ids)

            cursor_x += GAP_X

        # --- all_experts step ---
        elif "all_experts" in step:
            node_id = f"on{nid}"; nid += 1
            node = {
                "id": node_id,
                "x": cursor_x,
                "y": BASE_Y,
                "type": "expert",
                "tag": "all",
                "name": "全员讨论",
                "emoji": "👥",
                "temperature": 0.5,
                "instance": 1,
                "session_id": "",
                "author": "主持人",
                "content": "",
                "source": "",
            }
            nodes.append(node)
            groups.append({
                "id": f"og{gid}",
                "name": "👥 全员",
                "type": "all",
                "x": cursor_x - 20,
                "y": BASE_Y - 20,
                "w": 180,
                "h": 80,
                "nodeIds": [node_id],
            })
            gid += 1
            for pid in prev_node_ids:
                edges.append({"id": f"oe{eid}", "source": pid, "target": node_id})
                eid += 1
            prev_node_ids = [node_id]
            cursor_x += GAP_X

        # --- manual step ---
        elif "manual" in step:
            manual = step["manual"]
            node_id = f"on{nid}"; nid += 1
            _author = manual.get("author", "主持人") if isinstance(manual, dict) else "主持人"
            _content = manual.get("content", "") if isinstance(manual, dict) else ""
            if _author in ("begin", "bstart"):
                node = {
                    "id": node_id,
                    "x": cursor_x,
                    "y": BASE_Y,
                    "type": "manual", "tag": "manual",
                    "name": "开始", "emoji": "🚀",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "begin",
                    "content": _content,
                    "source": "",
                }
            elif _author == "bend":
                node = {
                    "id": node_id,
                    "x": cursor_x,
                    "y": BASE_Y,
                    "type": "manual", "tag": "manual",
                    "name": "结束", "emoji": "🏁",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": "bend",
                    "content": _content,
                    "source": "",
                }
            else:
                node = {
                    "id": node_id,
                    "x": cursor_x,
                    "y": BASE_Y,
                    "type": "manual", "tag": "manual",
                    "name": "手动注入", "emoji": "📝",
                    "temperature": 0, "instance": 1, "session_id": "",
                    "author": _author,
                    "content": _content,
                    "source": "",
                }
            nodes.append(node)
            for pid in prev_node_ids:
                edges.append({"id": f"oe{eid}", "source": pid, "target": node_id})
                eid += 1
            prev_node_ids = [node_id]
            cursor_x += GAP_X
        elif "script" in step:
            script = step["script"]
            node_id = f"on{nid}"; nid += 1
            if isinstance(script, dict):
                command = script.get("command", "")
                unix_command = script.get("unix_command", "")
                windows_command = script.get("windows_command", "")
                timeout = script.get("timeout", "")
                cwd = script.get("cwd", "")
            else:
                command = str(script or "")
                unix_command = ""
                windows_command = ""
                timeout = ""
                cwd = ""
            preview = unix_command or windows_command or command
            node = {
                "id": node_id,
                "x": cursor_x,
                "y": BASE_Y,
                "type": "script", "tag": "script",
                "name": "脚本节点", "emoji": "🧪",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": "script",
                "content": preview,
                "source": "",
                "script_command": command,
                "script_unix_command": unix_command,
                "script_windows_command": windows_command,
                "script_timeout": timeout,
                "script_cwd": cwd,
            }
            nodes.append(node)
            for pid in prev_node_ids:
                edges.append({"id": f"oe{eid}", "source": pid, "target": node_id})
                eid += 1
            prev_node_ids = [node_id]
            cursor_x += GAP_X
        elif "human" in step:
            human = step["human"]
            node_id = f"on{nid}"; nid += 1
            if isinstance(human, dict):
                prompt = human.get("prompt", "")
                author = human.get("author", "主持人")
                reply_to = human.get("reply_to", "")
            else:
                prompt = str(human or "")
                author = "主持人"
                reply_to = ""
            node = {
                "id": node_id,
                "x": cursor_x,
                "y": BASE_Y,
                "type": "human", "tag": "human",
                "name": "人类节点", "emoji": "🙋",
                "temperature": 0, "instance": 1, "session_id": "",
                "author": author,
                "content": prompt,
                "source": "",
                "human_prompt": prompt,
                "human_author": author,
                "human_reply_to": reply_to,
            }
            nodes.append(node)
            for pid in prev_node_ids:
                edges.append({"id": f"oe{eid}", "source": pid, "target": node_id})
                eid += 1
            prev_node_ids = [node_id]
            cursor_x += GAP_X

    layout = {
        "nodes": nodes,
        "edges": edges,
        "groups": groups,
        "settings": {
            "repeat": repeat,
            "max_rounds": 5,
            "cluster_threshold": 150,
        },
    }
    return layout
