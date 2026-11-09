"""
Town Genesis / Swarm blueprint generation for OASIS topics.

This is the lightweight bridge between Clawcross's existing OASIS discussion
runtime and a MiroFish-style "world generation" layer:

- build an immediate scaffold so Town can show a graph right away
- optionally ask the configured LLM to upgrade that scaffold into a richer
  prediction / GraphRAG-style blueprint
- normalize the output into a compact, frontend-friendly structure
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - optional fallback for lightweight test envs
    yaml = None
from langchain_core.messages import HumanMessage, SystemMessage

from oasis.experts import get_all_experts


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.join(_PROJECT_ROOT, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from services.llm_factory import create_chat_model, extract_text  # noqa: E402


_ALLOWED_NODE_TYPES = {"objective", "agent", "entity", "memory", "signal", "scenario"}
_NODE_GROUPS = {
    "objective": "objective",
    "agent": "agents",
    "entity": "entities",
    "memory": "memory",
    "signal": "signals",
    "scenario": "scenarios",
}


def _compact_text(value: Any, default: str = "", limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return default
    return text[:limit].strip()


def _slugify(value: Any, prefix: str = "node") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or prefix


def _ensure_unique_id(node_id: str, seen: set[str]) -> str:
    candidate = node_id
    idx = 2
    while candidate in seen:
        candidate = f"{node_id}-{idx}"
        idx += 1
    seen.add(candidate)
    return candidate


def _normalize_node_type(raw_type: Any) -> str:
    value = str(raw_type or "").strip().lower()
    alias_map = {
        "directive": "objective",
        "task": "objective",
        "goal": "objective",
        "agent_role": "agent",
        "persona": "agent",
        "actor": "agent",
        "person": "agent",
        "concept": "entity",
        "domain": "entity",
        "fact": "entity",
        "knowledge": "memory",
        "graphrag": "memory",
        "retrieval": "memory",
        "cluster": "memory",
        "indicator": "signal",
        "watchpoint": "signal",
        "outcome": "scenario",
        "forecast": "scenario",
        "branch": "scenario",
    }
    value = alias_map.get(value, value)
    return value if value in _ALLOWED_NODE_TYPES else "entity"


def _coerce_weight(value: Any, default: float = 0.55) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, weight))


def _listify_strings(values: Any, limit: int = 6) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _compact_text(item, "", 120)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _extract_seed_terms(question: str, limit: int = 4) -> list[str]:
    candidates = re.findall(r"[\u4e00-\u9fff]{2,10}|[A-Za-z][A-Za-z0-9_\-]{2,}", question or "")
    result: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        token = item.strip(" .,:;!?()[]{}\"'")
        if len(token) < 2:
            continue
        lowered = token.lower()
        if lowered in {"please", "could", "would", "should", "around", "about", "graph", "prediction"}:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(token)
        if len(result) >= limit:
            break
    return result or ["Signal", "Actors", "Context"]


def _extract_schedule_tags(schedule_yaml: str | None) -> list[str]:
    if not schedule_yaml:
        return []

    tags: list[str] = []
    seen: set[str] = set()

    def _push(tag: str):
        key = (tag or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        tags.append(key)

    def _walk(node: Any):
        if isinstance(node, str):
            match = re.match(r"([^#\s]+)#(?:temp|oasis|ext)#", node.strip())
            if match:
                _push(match.group(1))
            return
        if isinstance(node, list):
            for item in node:
                _walk(item)
            return
        if isinstance(node, dict):
            expert_ref = node.get("expert")
            if isinstance(expert_ref, str):
                match = re.match(r"([^#\s]+)#(?:temp|oasis|ext)#", expert_ref.strip())
                if match:
                    _push(match.group(1))
            for value in node.values():
                _walk(value)

    try:
        if yaml is not None:
            payload = yaml.safe_load(schedule_yaml)
            _walk(payload)
        else:
            raise RuntimeError("yaml unavailable")
    except Exception:
        for match in re.findall(r"([^#\s]+)#(?:temp|oasis|ext)#", schedule_yaml):
            _push(match)
    return tags


def _pick_expert_seed(user_id: str = "", team: str = "", schedule_yaml: str | None = None) -> list[dict[str, Any]]:
    configs = get_all_experts(user_id or None, team=team or "")
    by_tag = {str(item.get("tag") or "").strip(): item for item in configs if item.get("tag")}
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()

    for tag in _extract_schedule_tags(schedule_yaml):
        config = by_tag.get(tag)
        if not config:
            continue
        seen.add(tag)
        chosen.append(config)

    if not chosen:
        preferred = ["creative", "critical", "data", "synthesis"]
        for tag in preferred:
            config = by_tag.get(tag)
            if not config or tag in seen:
                continue
            seen.add(tag)
            chosen.append(config)

    if not chosen:
        chosen.extend(configs[:4])

    return chosen[:6]


def _build_discussion_excerpt(
    posts: list[dict[str, Any]] | None = None,
    timeline: list[dict[str, Any]] | None = None,
    conclusion: str = "",
) -> str:
    lines: list[str] = []
    for item in (posts or [])[-6:]:
        author = _compact_text(item.get("author"), "agent", 40)
        content = _compact_text(item.get("content"), "", 180)
        if content:
            lines.append(f"- {author}: {content}")
    for item in (timeline or [])[-4:]:
        event = _compact_text(item.get("event"), "event", 40)
        detail = _compact_text(item.get("detail"), "", 120)
        agent = _compact_text(item.get("agent"), "", 40)
        label = f"{event} / {agent}" if agent else event
        if detail:
            lines.append(f"- [{label}] {detail}")
    if conclusion:
        lines.append(f"- conclusion: {_compact_text(conclusion, '', 220)}")
    return "\n".join(lines[:10])


def _base_graph_nodes(question: str, expert_seed: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    terms = _extract_seed_terms(question)
    nodes: list[dict[str, Any]] = [
        {
            "id": "objective-core",
            "label": "Prediction Brief",
            "type": "objective",
            "summary": _compact_text(question, "Model the next-order effects of this request.", 180),
            "aliases": ["objective", "brief"],
            "meta": {"mode": "prediction"},
        },
        {
            "id": "signal-stack",
            "label": "Signal Stack",
            "type": "signal",
            "summary": "Incoming facts, weak signals, and trigger variables that should steer the simulation.",
            "aliases": ["signal", "trigger"],
            "meta": {"priority": "high"},
        },
        {
            "id": "memory-fabric",
            "label": "GraphRAG Memory",
            "type": "memory",
            "summary": "Shared retrieval memory that anchors agents to entities, relationships, and evolving evidence.",
            "aliases": ["memory", "graphrag"],
            "meta": {"collection": "world-memory"},
        },
        {
            "id": "scenario-base",
            "label": "Base Case",
            "type": "scenario",
            "summary": "Most likely path if the current forces keep compounding without a major shock.",
            "aliases": ["baseline", "default"],
            "meta": {"probability": "medium"},
        },
        {
            "id": "scenario-edge",
            "label": "Stress Case",
            "type": "scenario",
            "summary": "Adverse branch if sentiment flips, incentives diverge, or a hidden constraint dominates.",
            "aliases": ["edge", "risk"],
            "meta": {"probability": "low"},
        },
    ]
    edges: list[dict[str, Any]] = [
        {
            "id": "objective-to-signals",
            "source": "signal-stack",
            "target": "objective-core",
            "label": "steers",
            "kind": "informs",
            "weight": 0.9,
            "summary": "Signals define the causal inputs of the simulation.",
        },
        {
            "id": "objective-to-memory",
            "source": "objective-core",
            "target": "memory-fabric",
            "label": "indexes",
            "kind": "grounds",
            "weight": 0.78,
            "summary": "The brief is grounded in retrievable memory blocks.",
        },
        {
            "id": "basecase-link",
            "source": "objective-core",
            "target": "scenario-base",
            "label": "branches",
            "kind": "forecast",
            "weight": 0.72,
            "summary": "The model should produce a probable baseline outcome.",
        },
        {
            "id": "stresscase-link",
            "source": "objective-core",
            "target": "scenario-edge",
            "label": "stress-tests",
            "kind": "forecast",
            "weight": 0.66,
            "summary": "The model should also surface edge-case dynamics.",
        },
    ]

    seen_ids = {node["id"] for node in nodes}
    for idx, expert in enumerate(expert_seed[:4], start=1):
        tag = _compact_text(expert.get("tag"), f"expert-{idx}", 32)
        name = _compact_text(expert.get("name"), tag, 48)
        node_id = _ensure_unique_id(_slugify(f"agent-{tag}", "agent"), seen_ids)
        nodes.append(
            {
                "id": node_id,
                "label": name,
                "type": "agent",
                "summary": _compact_text(expert.get("description") or expert.get("persona"), "", 180)
                or f"{name} challenges the topic from the {tag} angle.",
                "aliases": [tag, name],
                "meta": {"tag": tag, "source": expert.get("source", "public")},
            }
        )
        edges.extend(
            [
                {
                    "id": f"{node_id}-objective",
                    "source": "objective-core",
                    "target": node_id,
                    "label": "delegates",
                    "kind": "assignment",
                    "weight": 0.82,
                    "summary": f"{name} is asked to reason over the core brief.",
                },
                {
                    "id": f"{node_id}-memory",
                    "source": node_id,
                    "target": "memory-fabric",
                    "label": "queries",
                    "kind": "retrieval",
                    "weight": 0.71,
                    "summary": f"{name} should retrieve world context before posting.",
                },
            ]
        )

    for idx, term in enumerate(terms[:4], start=1):
        node_id = _ensure_unique_id(_slugify(f"entity-{term}", "entity"), seen_ids)
        nodes.append(
            {
                "id": node_id,
                "label": _compact_text(term, f"Entity {idx}", 42),
                "type": "entity",
                "summary": f"One of the anchor entities or forces implied by the user instruction: {term}.",
                "aliases": [term],
                "meta": {"rank": idx},
            }
        )
        edges.extend(
            [
                {
                    "id": f"{node_id}-signal",
                    "source": node_id,
                    "target": "signal-stack",
                    "label": "surfaces",
                    "kind": "evidence",
                    "weight": 0.64,
                    "summary": f"{term} should be tracked as an evolving signal source.",
                },
                {
                    "id": f"{node_id}-scenario",
                    "source": node_id,
                    "target": "scenario-base" if idx % 2 else "scenario-edge",
                    "label": "influences",
                    "kind": "causal",
                    "weight": 0.58,
                    "summary": f"{term} shifts the scenario path if its state changes.",
                },
            ]
        )
    return nodes, edges


def build_pending_swarm(
    question: str,
    *,
    user_id: str = "",
    team: str = "",
    schedule_yaml: str | None = None,
    mode: str = "prediction",
) -> dict[str, Any]:
    expert_seed = _pick_expert_seed(user_id=user_id, team=team, schedule_yaml=schedule_yaml)
    nodes, edges = _base_graph_nodes(question, expert_seed)
    return {
        "version": 1,
        "status": "pending",
        "mode": _compact_text(mode, "prediction", 24),
        "source": "scaffold",
        "summary": "Forging a Town Genesis blueprint from the prompt and current expert roster.",
        "objective": _compact_text(question, "", 220),
        "time_horizon": "Exploratory",
        "prediction": "The engine is building a first-pass swarm world and GraphRAG memory map.",
        "signals": _extract_seed_terms(question, limit=4),
        "watchouts": [
            "Hidden stakeholders may not yet be represented.",
            "Scenario weights should be treated as exploratory until refreshed.",
        ],
        "scenarios": [
            {
                "label": "Base Case",
                "summary": "Likely trajectory if incentives remain stable.",
                "probability": "medium",
            },
            {
                "label": "Stress Case",
                "summary": "Adverse branch if one critical force turns against the current trend.",
                "probability": "low",
            },
        ],
        "nudges": [
            "Add a stronger external shock or policy change to stress-test the world.",
            "Ask one agent to argue from downside risk instead of consensus.",
        ],
        "graphrag": {
            "collections": ["world-memory", "agent-intent", "signal-watch"],
            "queries": [
                "Which actors have the highest leverage?",
                "What hidden constraints can flip the outcome?",
            ],
            "memories": [
                "Seed brief",
                "Working assumptions",
                "Observed signals",
            ],
        },
        "agents": [
            {
                "tag": _compact_text(expert.get("tag"), "", 32),
                "name": _compact_text(expert.get("name"), "", 48),
                "summary": _compact_text(expert.get("description") or expert.get("persona"), "", 160),
            }
            for expert in expert_seed[:4]
        ],
        "graph": {
            "nodes": nodes,
            "edges": edges,
        },
        "generated_at": time.time(),
    }


def _build_fallback_swarm(
    question: str,
    *,
    user_id: str = "",
    team: str = "",
    schedule_yaml: str | None = None,
    mode: str = "prediction",
    error: str = "",
) -> dict[str, Any]:
    payload = build_pending_swarm(
        question,
        user_id=user_id,
        team=team,
        schedule_yaml=schedule_yaml,
        mode=mode,
    )
    payload["status"] = "ready"
    payload["source"] = "fallback"
    payload["summary"] = "Built a deterministic swarm world from the prompt and expert topology."
    payload["prediction"] = "Use this as a first-pass world model; refresh once the discussion accumulates more evidence."
    if error:
        payload["diagnostics"] = {"llm_error": _compact_text(error, "", 300)}
    return payload


def _parse_json_payload(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _normalize_scenarios(raw: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return result
    for item in raw:
        if isinstance(item, str):
            label = _compact_text(item, "", 48)
            if not label:
                continue
            result.append({"label": label, "summary": label, "probability": "medium"})
            continue
        if not isinstance(item, dict):
            continue
        label = _compact_text(item.get("label") or item.get("name"), "", 48)
        summary = _compact_text(item.get("summary") or item.get("detail"), label, 160)
        probability = _compact_text(item.get("probability") or item.get("weight"), "medium", 20).lower()
        if not label:
            continue
        if probability not in {"low", "medium", "high"}:
            probability = "medium"
        result.append({"label": label, "summary": summary, "probability": probability})
        if len(result) >= 4:
            break
    return result


def _normalize_graphrag(raw: Any, seed_terms: list[str]) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        raw = {}
    collections = _listify_strings(raw.get("collections") or raw.get("stores"), 5)
    queries = _listify_strings(raw.get("queries") or raw.get("retrieval_queries"), 5)
    memories = _listify_strings(raw.get("memories") or raw.get("shortcuts"), 5)
    if not collections:
        collections = ["world-memory", "agent-intent", "signal-watch"]
    if not queries:
        queries = [
            "Which nodes dominate the current trajectory?",
            "What evidence would invalidate the baseline scenario?",
        ]
    if not memories:
        memories = seed_terms[:3] or ["Seed brief", "Signals", "Scenarios"]
    return {"collections": collections, "queries": queries, "memories": memories}


def _normalize_graph(
    raw_graph: Any,
    *,
    question: str,
    expert_seed: list[dict[str, Any]],
    seed_terms: list[str],
) -> dict[str, list[dict[str, Any]]]:
    raw_graph = raw_graph if isinstance(raw_graph, dict) else {}
    raw_nodes = raw_graph.get("nodes")
    raw_edges = raw_graph.get("edges")
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    if isinstance(raw_nodes, list):
        for item in raw_nodes[:20]:
            if not isinstance(item, dict):
                continue
            label = _compact_text(item.get("label") or item.get("name"), "", 48)
            if not label:
                continue
            node_type = _normalize_node_type(item.get("type") or item.get("kind") or item.get("group"))
            node_id = _ensure_unique_id(_slugify(item.get("id") or label, node_type), seen_ids)
            aliases = _listify_strings(item.get("aliases") or item.get("names"), 5)
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            nodes.append(
                {
                    "id": node_id,
                    "label": label,
                    "type": node_type,
                    "summary": _compact_text(item.get("summary") or item.get("description"), label, 180),
                    "aliases": aliases,
                    "meta": {
                        key: _compact_text(value, "", 80)
                        for key, value in meta.items()
                        if _compact_text(value, "", 80)
                    },
                }
            )

    label_to_id = {_slugify(node["label"], node["type"]): node["id"] for node in nodes}
    label_to_id.update({_slugify(node["id"], node["type"]): node["id"] for node in nodes})
    known_ids = {node["id"] for node in nodes}

    if isinstance(raw_edges, list):
        for idx, item in enumerate(raw_edges[:30], start=1):
            if not isinstance(item, dict):
                continue
            source = _compact_text(item.get("source"), "", 80)
            target = _compact_text(item.get("target"), "", 80)
            if not source or not target:
                continue
            source_id = source if source in known_ids else label_to_id.get(_slugify(source))
            target_id = target if target in known_ids else label_to_id.get(_slugify(target))
            if not source_id or not target_id or source_id == target_id:
                continue
            edges.append(
                {
                    "id": _compact_text(item.get("id"), f"edge-{idx}", 48),
                    "source": source_id,
                    "target": target_id,
                    "label": _compact_text(item.get("label") or item.get("name"), "links", 36),
                    "kind": _compact_text(item.get("kind") or item.get("type"), "causal", 32),
                    "weight": _coerce_weight(item.get("weight"), 0.55),
                    "summary": _compact_text(item.get("summary") or item.get("reason"), "", 140),
                }
            )

    if not any(node["type"] == "objective" for node in nodes):
        nodes.insert(
            0,
            {
                "id": _ensure_unique_id("objective-core", seen_ids),
                "label": "Prediction Brief",
                "type": "objective",
                "summary": _compact_text(question, "", 180),
                "aliases": ["objective", "brief"],
                "meta": {"mode": "prediction"},
            },
        )
        known_ids = {node["id"] for node in nodes}

    objective_id = next((node["id"] for node in nodes if node["type"] == "objective"), "objective-core")
    node_labels = {node["label"].lower(): node["id"] for node in nodes}
    agent_tags_in_graph = {
        str(node.get("meta", {}).get("tag", "")).strip()
        for node in nodes
        if node["type"] == "agent"
    }
    for expert in expert_seed[:4]:
        name = _compact_text(expert.get("name"), "", 48)
        tag = _compact_text(expert.get("tag"), "", 32)
        if not name or (name.lower() in node_labels or tag in agent_tags_in_graph):
            continue
        node_id = _ensure_unique_id(_slugify(f"agent-{tag}", "agent"), seen_ids)
        nodes.append(
            {
                "id": node_id,
                "label": name,
                "type": "agent",
                "summary": _compact_text(expert.get("description") or expert.get("persona"), "", 160)
                or f"{name} reasons from the {tag} perspective.",
                "aliases": [name, tag],
                "meta": {"tag": tag},
            }
        )
        edges.append(
            {
                "id": f"{node_id}-objective",
                "source": objective_id,
                "target": node_id,
                "label": "delegates",
                "kind": "assignment",
                "weight": 0.82,
                "summary": f"{name} receives a role in the swarm.",
            }
        )

    if len(nodes) < 6:
        fallback_nodes, fallback_edges = _base_graph_nodes(question, expert_seed)
        existing = {node["id"] for node in nodes}
        for node in fallback_nodes:
            if node["id"] in existing:
                continue
            nodes.append(node)
            existing.add(node["id"])
        edges.extend(fallback_edges)

    if not edges:
        scenario_id = next((node["id"] for node in nodes if node["type"] == "scenario"), None)
        memory_id = next((node["id"] for node in nodes if node["type"] == "memory"), None)
        for node in nodes:
            if node["type"] == "agent":
                edges.append(
                    {
                        "id": f"{node['id']}-fallback-objective",
                        "source": objective_id,
                        "target": node["id"],
                        "label": "delegates",
                        "kind": "assignment",
                        "weight": 0.7,
                        "summary": "Default assignment edge.",
                    }
                )
                if memory_id:
                    edges.append(
                        {
                            "id": f"{node['id']}-fallback-memory",
                            "source": node["id"],
                            "target": memory_id,
                            "label": "queries",
                            "kind": "retrieval",
                            "weight": 0.58,
                            "summary": "Default memory retrieval edge.",
                        }
                    )
                if scenario_id:
                    edges.append(
                        {
                            "id": f"{node['id']}-fallback-scenario",
                            "source": node["id"],
                            "target": scenario_id,
                            "label": "pushes",
                            "kind": "forecast",
                            "weight": 0.55,
                            "summary": "Default scenario influence edge.",
                        }
                    )

    return {"nodes": nodes[:20], "edges": edges[:36]}


def _normalize_blueprint(
    raw: dict[str, Any] | None,
    *,
    question: str,
    user_id: str = "",
    team: str = "",
    schedule_yaml: str | None = None,
    mode: str = "prediction",
) -> dict[str, Any]:
    expert_seed = _pick_expert_seed(user_id=user_id, team=team, schedule_yaml=schedule_yaml)
    seed_terms = _extract_seed_terms(question)
    if not raw:
        return _build_fallback_swarm(
            question,
            user_id=user_id,
            team=team,
            schedule_yaml=schedule_yaml,
            mode=mode,
            error="Empty LLM response",
        )

    payload = raw.get("swarm") if isinstance(raw.get("swarm"), dict) else raw
    graph = _normalize_graph(
        payload.get("graph"),
        question=question,
        expert_seed=expert_seed,
        seed_terms=seed_terms,
    )
    scenarios = _normalize_scenarios(payload.get("scenarios"))
    if not scenarios:
        scenarios = [
            {"label": "Base Case", "summary": "Likely path with current forces intact.", "probability": "medium"},
            {"label": "Stress Case", "summary": "Adverse branch if one key assumption breaks.", "probability": "low"},
        ]

    nudges = _listify_strings(payload.get("nudges") or payload.get("suggested_nudges"), 5)
    if not nudges:
        nudges = [
            "Inject a stronger external variable and compare scenario drift.",
            "Ask one agent to attack the baseline assumptions.",
        ]

    signals = _listify_strings(payload.get("signals") or payload.get("drivers"), 5)
    if not signals:
        signals = seed_terms[:4]

    watchouts = _listify_strings(payload.get("watchouts") or payload.get("risks"), 5)
    if not watchouts:
        watchouts = [
            "Some stakeholders may still be missing from the graph.",
            "Scenario confidence is exploratory rather than calibrated.",
        ]

    result = {
        "version": 1,
        "status": "ready",
        "mode": _compact_text(payload.get("mode"), _compact_text(mode, "prediction", 24), 24),
        "source": "llm",
        "summary": _compact_text(
            payload.get("summary") or payload.get("engine_summary"),
            "Generated a Town Genesis swarm blueprint.",
            220,
        ),
        "objective": _compact_text(payload.get("objective"), _compact_text(question, "", 220), 220),
        "time_horizon": _compact_text(payload.get("time_horizon") or payload.get("horizon"), "Exploratory", 48),
        "prediction": _compact_text(
            payload.get("prediction") or payload.get("report") or payload.get("outlook"),
            "Use this swarm as a living forecast model and refresh it as new evidence appears.",
            240,
        ),
        "signals": signals,
        "watchouts": watchouts,
        "scenarios": scenarios,
        "nudges": nudges,
        "graphrag": _normalize_graphrag(payload.get("graphrag"), seed_terms),
        "agents": [
            {
                "tag": _compact_text(expert.get("tag"), "", 32),
                "name": _compact_text(expert.get("name"), "", 48),
                "summary": _compact_text(expert.get("description") or expert.get("persona"), "", 160),
            }
            for expert in expert_seed[:4]
        ],
        "graph": graph,
        "generated_at": time.time(),
    }
    return result


def generate_swarm_blueprint(
    question: str,
    *,
    user_id: str = "",
    team: str = "",
    schedule_yaml: str | None = None,
    posts: list[dict[str, Any]] | None = None,
    timeline: list[dict[str, Any]] | None = None,
    conclusion: str = "",
    mode: str = "prediction",
) -> dict[str, Any]:
    expert_seed = _pick_expert_seed(user_id=user_id, team=team, schedule_yaml=schedule_yaml)
    fallback = _build_fallback_swarm(
        question,
        user_id=user_id,
        team=team,
        schedule_yaml=schedule_yaml,
        mode=mode,
    )
    expert_lines = []
    for expert in expert_seed[:5]:
        expert_lines.append(
            f"- tag={_compact_text(expert.get('tag'), '', 24)}"
            f" name={_compact_text(expert.get('name'), '', 40)}"
            f" persona={_compact_text(expert.get('description') or expert.get('persona'), '', 120)}"
        )

    discussion_excerpt = _build_discussion_excerpt(posts=posts, timeline=timeline, conclusion=conclusion)
    system_prompt = (
        "You design compact multi-agent prediction blueprints for Clawcross OASIS Town.\n"
        "Return JSON only. No markdown, no prose.\n\n"
        "JSON schema:\n"
        "{\n"
        '  "summary": "one paragraph",\n'
        '  "objective": "what the swarm should predict",\n'
        '  "time_horizon": "short phrase",\n'
        '  "prediction": "one compact forecast paragraph",\n'
        '  "signals": ["signal 1", "signal 2"],\n'
        '  "watchouts": ["risk 1", "risk 2"],\n'
        '  "scenarios": [{"label": "...", "summary": "...", "probability": "low|medium|high"}],\n'
        '  "nudges": ["operator suggestion 1", "operator suggestion 2"],\n'
        '  "graphrag": {"collections": ["..."], "queries": ["..."], "memories": ["..."]},\n'
        '  "graph": {\n'
        '    "nodes": [{"id": "...", "label": "...", "type": "objective|agent|entity|memory|signal|scenario", "summary": "...", "aliases": ["..."], "meta": {"tag": "...", "role": "..."}}],\n'
        '    "edges": [{"id": "...", "source": "...", "target": "...", "label": "...", "kind": "...", "weight": 0.62, "summary": "..."}]\n'
        "  }\n"
        "}\n\n"
        "Rules:\n"
        "- Keep labels short.\n"
        "- Include one objective node, 3-5 agent nodes, 2-5 entity nodes, at least one memory node, one signal node, and two scenario nodes.\n"
        "- Agent nodes must correspond to the available expert roster.\n"
        "- Design the graph for interactive exploration, not for academic completeness.\n"
        "- GraphRAG collections/queries should sound usable inside a live sandbox.\n"
    )
    user_prompt = (
        f"question:\n{_compact_text(question, '', 500)}\n\n"
        f"mode: {_compact_text(mode, 'prediction', 24)}\n\n"
        "available experts:\n"
        f"{chr(10).join(expert_lines) or '- creative / critical / data / synthesis'}\n\n"
        "recent discussion excerpt:\n"
        f"{discussion_excerpt or '- none yet'}\n"
    )

    try:
        llm = create_chat_model(temperature=0.25, max_tokens=2600, timeout=45, max_retries=1)
        response = llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
        )
        text = extract_text(getattr(response, "content", response))
        parsed = _parse_json_payload(text)
        result = _normalize_blueprint(
            parsed,
            question=question,
            user_id=user_id,
            team=team,
            schedule_yaml=schedule_yaml,
            mode=mode,
        )
        return result
    except Exception as exc:
        fallback["diagnostics"] = {"llm_error": _compact_text(exc, "", 300)}
        return fallback
