"""
OASIS Forum - Discussion Scheduler (Graph Engine)

Unified directed graph model for workflow orchestration.
All workflows — linear, parallel, branching, looping — are expressed
as a single graph of nodes connected by edges (fixed or conditional).

The engine uses a **Pregel-style super-step iteration** to execute
the graph:
  1. Identify all nodes whose incoming edges are satisfied (triggers ready)
  2. Execute those nodes in parallel
  3. Evaluate outgoing edges (fixed edges always fire; conditional edges
     call a route function to pick target)
  4. Repeat until no more nodes are activated or max_steps exceeded

YAML format (new unified graph):
  version: 2
  plan:
    # Every node has a unique id
    - id: analyze
      expert: "analyst#temp#1"
      instruction: "分析需求"

    - id: review
      expert: "reviewer#temp#1"

    - id: implement
      expert: "coder#temp#1"

    - id: final
      expert: "writer#temp#1"

  # Fixed edges: always fire when source completes
  edges:
    - [analyze, review]           # analyze → review
    - [review, implement]         # review → implement

  # Conditional edges: route function determines target
  conditional_edges:
    - source: implement
      condition: "last_post_contains:LGTM"
      then: final                 # condition true → go to final
      else: review                # condition false → loop back to review

Backward compatibility:
  - version: 1 YAML (no edges/conditional_edges) is auto-converted:
    - Steps without 'id' get auto-generated IDs (_step_0, _step_1, ...)
    - Linear mode (no depends_on): sequential edges are auto-created
    - DAG mode (has depends_on): depends_on is converted to edges

Expert name format:
  "tag#temp#N"          → ExpertAgent (stateless LLM)
  "tag#oasis#name"      → SessionExpert (name→session lookup, tag→persona)
  "#oasis#name"         → SessionExpert (name→session lookup, no tag)
  "name#ext#id"         → ExternalExpert
  Any name + "#new"     → force new session
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import os

import yaml

# Placeholder mask used in YAML to indicate "use key from environment"
_API_KEY_MASK = "****"

# Special node IDs for graph entry/exit
START = "__start__"
END = "__end__"

# Safety limits
MAX_SUPER_STEPS = 200  # max Pregel iterations before forced stop


class StepType(str, Enum):
    """Types of schedule steps (graph nodes)."""
    EXPERT = "expert"           # Single expert speaks
    PARALLEL = "parallel"       # Multiple experts speak in parallel
    ALL = "all_experts"         # All experts speak in parallel
    MANUAL = "manual"           # Inject a post manually (no LLM)
    SCRIPT = "script"           # Run a platform command via subprocess
    HUMAN = "human"             # Wait for a human reply


@dataclass
class ScheduleStep:
    """A node in the execution graph."""
    step_type: StepType
    node_id: str = ""                                        # unique node identifier (required)
    expert_names: list[str] = field(default_factory=list)    # for EXPERT / PARALLEL
    instructions: dict[str, str] = field(default_factory=dict)  # expert_name → instruction text
    manual_author: str = ""                                  # for MANUAL
    manual_content: str = ""                                 # for MANUAL
    manual_reply_to: Optional[int] = None                    # for MANUAL
    script_command: str = ""                                 # generic script command
    script_unix_command: str = ""                            # unix-specific command
    script_windows_command: str = ""                         # windows-specific command
    script_timeout: Optional[float] = None                   # script timeout in seconds
    script_cwd: str = ""                                     # optional working directory
    human_prompt: str = ""                                   # for HUMAN
    human_author: str = ""                                   # author shown for human prompt
    human_reply_to: Optional[int] = None                     # explicit reply target
    # External agent config: expert_name → {api_url, api_key, model}
    external_configs: dict[str, dict] = field(default_factory=dict)
    # Selector node: this node acts as a LLM-powered router
    is_selector: bool = False


@dataclass
class Edge:
    """A fixed edge: always fires when source completes."""
    source: str
    target: str


@dataclass
class ConditionalEdge:
    """A conditional edge: evaluates condition to pick target.

    Supported condition expressions:
      last_post_contains:<keyword>       — last post content contains keyword
      last_post_not_contains:<keyword>   — last post does NOT contain keyword
      post_count_gte:<N>                 — total post count >= N
      post_count_lt:<N>                  — total post count < N
      always                             — always true (unconditional)
      !<expr>                            — negate any expression
    """
    source: str
    condition: str
    then_target: str       # target when condition is true
    else_target: str = ""  # target when condition is false (empty = no edge)


@dataclass
class SelectorEdge:
    """A selector edge: LLM-powered routing.

    The source node (must have is_selector=True) will be prompted with
    the available choices and asked to output a JSON object:
    {"clawcross_type": "oasis choose", "choose": N, "content": "reason"}.
    The engine parses N and activates the corresponding target node.
    """
    source: str
    choices: dict[int, str] = field(default_factory=dict)  # choice_number → target_node_id


@dataclass
class Schedule:
    """Parsed schedule: a graph of nodes + edges."""
    nodes: list[ScheduleStep]                       # all execution nodes
    edges: list[Edge] = field(default_factory=list)  # fixed edges
    conditional_edges: list[ConditionalEdge] = field(default_factory=list)  # conditional edges
    selector_edges: list[SelectorEdge] = field(default_factory=list)  # LLM selector edges
    repeat: bool = False      # True = wrap entire graph in a repeat loop
    max_repeat: int = 1       # how many times to repeat (only if repeat=True)
    discussion: bool = False  # True = forum discussion mode; False = execute mode

    # Derived: populated after parsing
    node_map: dict[str, ScheduleStep] = field(default_factory=dict)
    # source_id → list of Edge/ConditionalEdge
    out_edges: dict[str, list[Edge]] = field(default_factory=dict)
    out_cond_edges: dict[str, list[ConditionalEdge]] = field(default_factory=dict)
    # source_id → SelectorEdge (at most one per selector node)
    out_selector_edges: dict[str, SelectorEdge] = field(default_factory=dict)
    # target_id → set of source_ids (for trigger checking)
    in_sources: dict[str, set[str]] = field(default_factory=dict)
    # entry nodes: nodes reachable from START
    entry_nodes: list[str] = field(default_factory=list)

    def build_indexes(self):
        """Build lookup indexes from nodes/edges. Call after parsing."""
        self.node_map = {n.node_id: n for n in self.nodes}
        self.out_edges = {}
        self.out_cond_edges = {}
        self.in_sources = {n.node_id: set() for n in self.nodes}
        self.in_sources[END] = set()

        for e in self.edges:
            self.out_edges.setdefault(e.source, []).append(e)
            if e.target != END and e.target in self.in_sources:
                self.in_sources[e.target].add(e.source)
            elif e.target == END:
                self.in_sources[END].add(e.source)

        for ce in self.conditional_edges:
            self.out_cond_edges.setdefault(ce.source, []).append(ce)
            # Conditional edge targets are NOT added to in_sources.
            # in_sources only tracks fixed-edge predecessors so that
            # the trigger check ("all fixed-edge sources completed") is
            # not polluted by conditional back-edges / loop edges.
            # Same rule applies to selector edges below.

        for ce in self.conditional_edges:
            for t in [ce.then_target, ce.else_target]:
                if t == END:
                    self.in_sources[END].add(ce.source)

        for se in self.selector_edges:
            self.out_selector_edges[se.source] = se
            # Selector edge targets are NOT added to in_sources (same rationale).
            for t in se.choices.values():
                if t == END:
                    self.in_sources[END].add(se.source)

        # Entry nodes: nodes with no incoming edges from other nodes.
        # A node is NOT an entry node if it is the target of ANY edge type
        # (fixed, conditional, or selector).  in_sources only tracks fixed
        # edges, so we must also check conditional/selector targets.
        cond_sel_targets: set[str] = set()
        for ce in self.conditional_edges:
            if ce.then_target and ce.then_target != END:
                cond_sel_targets.add(ce.then_target)
            if ce.else_target and ce.else_target != END:
                cond_sel_targets.add(ce.else_target)
        for se in self.selector_edges:
            for t in se.choices.values():
                if t and t != END:
                    cond_sel_targets.add(t)

        self.entry_nodes = []
        for n in self.nodes:
            nid = n.node_id
            if not self.in_sources[nid] and nid not in cond_sel_targets:
                self.entry_nodes.append(nid)

    @property
    def steps(self) -> list[ScheduleStep]:
        """Backward compatible alias for nodes."""
        return self.nodes


def _extract_external_config(item: dict) -> dict:
    """Extract external agent config fields from a YAML step item."""
    cfg: dict = {}
    if "api_url" in item:
        cfg["api_url"] = str(item["api_url"])
    if "api_key" in item:
        raw_key = str(item["api_key"])
        if raw_key == _API_KEY_MASK:
            cfg["api_key"] = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
        else:
            cfg["api_key"] = raw_key
    if "model" in item:
        cfg["model"] = str(item["model"])
    if "headers" in item and isinstance(item["headers"], dict):
        cfg["headers"] = {str(k): str(v) for k, v in item["headers"].items()}
    return cfg


def _parse_node(i: int, item: dict) -> ScheduleStep:
    """Parse a single YAML step dict into a ScheduleStep node."""
    if not isinstance(item, dict):
        raise ValueError(f"Step {i}: must be a dict, got {type(item).__name__}")

    node_id = str(item.get("id", ""))

    is_selector = bool(item.get("selector", False))

    if "expert" in item:
        expert_name = str(item["expert"])
        instr_map = {}
        ext_configs = {}
        if "instruction" in item:
            instr_map[expert_name] = str(item["instruction"])
        if "api_url" in item or "headers" in item or "model" in item:
            ext_configs[expert_name] = _extract_external_config(item)
        return ScheduleStep(
            step_type=StepType.EXPERT,
            node_id=node_id,
            expert_names=[expert_name],
            instructions=instr_map,
            external_configs=ext_configs,
            is_selector=is_selector,
        )

    elif "parallel" in item:
        names = []
        instr_map = {}
        ext_configs = {}
        for sub in item["parallel"]:
            if isinstance(sub, dict) and "expert" in sub:
                ename = str(sub["expert"])
                names.append(ename)
                if "instruction" in sub:
                    instr_map[ename] = str(sub["instruction"])
                if "api_url" in sub or "headers" in sub or "model" in sub:
                    ext_configs[ename] = _extract_external_config(sub)
            elif isinstance(sub, str):
                names.append(sub)
            else:
                raise ValueError(f"Step {i}: parallel entries must have 'expert' key")
        if not names:
            raise ValueError(f"Step {i}: parallel list is empty")
        return ScheduleStep(
            step_type=StepType.PARALLEL,
            node_id=node_id,
            expert_names=names,
            instructions=instr_map,
            external_configs=ext_configs,
        )

    elif "all_experts" in item:
        return ScheduleStep(
            step_type=StepType.ALL,
            node_id=node_id,
        )

    elif "manual" in item:
        m = item["manual"]
        if not isinstance(m, dict) or "content" not in m:
            raise ValueError(f"Step {i}: manual must have 'content'")
        return ScheduleStep(
            step_type=StepType.MANUAL,
            node_id=node_id,
            manual_author=str(m.get("author", "主持人")),
            manual_content=str(m["content"]),
            manual_reply_to=m.get("reply_to"),
        )

    elif "script" in item:
        s = item["script"]
        if isinstance(s, str):
            return ScheduleStep(
                step_type=StepType.SCRIPT,
                node_id=node_id,
                script_command=s,
            )
        if not isinstance(s, dict):
            raise ValueError(f"Step {i}: script must be a string or dict")
        if not any(s.get(k) for k in ("command", "unix_command", "windows_command")):
            raise ValueError(f"Step {i}: script must provide command/unix_command/windows_command")
        timeout = s.get("timeout")
        return ScheduleStep(
            step_type=StepType.SCRIPT,
            node_id=node_id,
            script_command=str(s.get("command", "")),
            script_unix_command=str(s.get("unix_command", "")),
            script_windows_command=str(s.get("windows_command", "")),
            script_timeout=float(timeout) if timeout is not None else None,
            script_cwd=str(s.get("cwd", "")),
        )

    elif "human" in item:
        h = item["human"]
        if isinstance(h, str):
            return ScheduleStep(
                step_type=StepType.HUMAN,
                node_id=node_id,
                human_prompt=h,
                human_author="主持人",
            )
        if not isinstance(h, dict) or "prompt" not in h:
            raise ValueError(f"Step {i}: human must have 'prompt'")
        return ScheduleStep(
            step_type=StepType.HUMAN,
            node_id=node_id,
            human_prompt=str(h["prompt"]),
            human_author=str(h.get("author", "主持人")),
            human_reply_to=h.get("reply_to"),
        )

    else:
        raise ValueError(f"Step {i}: unknown step type, keys={list(item.keys())}")


def parse_schedule(yaml_content: str) -> Schedule:
    """Parse a YAML schedule string into a Schedule (graph) object.

    Supports three YAML formats:
      1. New graph format (version: 2): nodes + edges + conditional_edges
      2. Legacy DAG (version: 1, steps have 'id' + 'depends_on'): auto-converted
      3. Legacy linear (version: 1, no ids): auto-converted to sequential graph

    Raises ValueError on invalid format.
    """
    data = yaml.safe_load(yaml_content)
    if not isinstance(data, dict) or "plan" not in data:
        raise ValueError("Schedule YAML must contain a 'plan' key")

    plan = data["plan"]
    if not isinstance(plan, list):
        raise ValueError("'plan' must be a list of steps")

    repeat = bool(data.get("repeat", False))
    discussion = bool(data.get("discussion", False))
    version = int(data.get("version", 1))

    # Parse all nodes
    nodes: list[ScheduleStep] = []
    for i, item in enumerate(plan):
        node = _parse_node(i, item)
        nodes.append(node)

    edges: list[Edge] = []
    conditional_edges: list[ConditionalEdge] = []
    selector_edges: list[SelectorEdge] = []

    if version >= 2 or "edges" in data or "conditional_edges" in data or "selector_edges" in data:
        # ── New graph format: explicit edges ──
        # Ensure all nodes have IDs
        for i, n in enumerate(nodes):
            if not n.node_id:
                raise ValueError(f"Step {i}: all nodes must have 'id' in graph mode (version >= 2)")

        # Parse fixed edges
        raw_edges = data.get("edges", [])
        if isinstance(raw_edges, list):
            for ei, e in enumerate(raw_edges):
                if isinstance(e, list) and len(e) == 2:
                    edges.append(Edge(source=str(e[0]), target=str(e[1])))
                elif isinstance(e, dict):
                    edges.append(Edge(
                        source=str(e.get("source", "")),
                        target=str(e.get("target", "")),
                    ))
                else:
                    raise ValueError(f"Edge {ei}: must be [source, target] or {{source, target}}")

        # Parse conditional edges
        raw_cond = data.get("conditional_edges", [])
        if isinstance(raw_cond, list):
            for ci, ce in enumerate(raw_cond):
                if not isinstance(ce, dict):
                    raise ValueError(f"ConditionalEdge {ci}: must be a dict")
                conditional_edges.append(ConditionalEdge(
                    source=str(ce.get("source", "")),
                    condition=str(ce.get("condition", "")),
                    then_target=str(ce.get("then", "")),
                    else_target=str(ce.get("else", "")),
                ))

        # Parse selector edges
        selector_edges: list[SelectorEdge] = []
        raw_sel = data.get("selector_edges", [])
        if isinstance(raw_sel, list):
            for si, se in enumerate(raw_sel):
                if not isinstance(se, dict):
                    raise ValueError(f"SelectorEdge {si}: must be a dict")
                choices_raw = se.get("choices", {})
                choices: dict[int, str] = {}
                for k, v in choices_raw.items():
                    choices[int(k)] = str(v)
                selector_edges.append(SelectorEdge(
                    source=str(se.get("source", "")),
                    choices=choices,
                ))

    else:
        # ── Legacy format: auto-convert to graph ──
        has_any_id = any(n.node_id for n in nodes)
        has_depends_on = any(
            item.get("depends_on") for item in plan if isinstance(item, dict)
        )

        if has_any_id or has_depends_on:
            # Legacy DAG mode: convert depends_on to edges
            for i, (node, item) in enumerate(zip(nodes, plan)):
                if not node.node_id:
                    node.node_id = f"_step_{i}"

                depends_on_raw = item.get("depends_on", []) if isinstance(item, dict) else []
                if isinstance(depends_on_raw, str):
                    depends_on_raw = [depends_on_raw]
                elif not isinstance(depends_on_raw, list):
                    depends_on_raw = []

                for dep in depends_on_raw:
                    edges.append(Edge(source=str(dep), target=node.node_id))

            # Nodes with no incoming edges → they are entry points (no edge needed)
            # We'll let build_indexes handle entry_nodes detection

        else:
            # Legacy linear mode: auto-generate IDs and sequential edges
            for i, node in enumerate(nodes):
                node.node_id = f"_step_{i}"

            # Create sequential chain: _step_0 → _step_1 → ... → _step_N → END
            for i in range(len(nodes) - 1):
                edges.append(Edge(source=nodes[i].node_id, target=nodes[i + 1].node_id))
            if nodes:
                edges.append(Edge(source=nodes[-1].node_id, target=END))

    # Compute max_repeat for backward compat
    max_repeat = 1
    if repeat:
        # In legacy mode, max_repeat comes from engine's max_rounds
        # We store it as a hint; engine will handle the actual repeat logic
        max_repeat = int(data.get("max_repeat", 0))  # 0 = use engine default

    schedule = Schedule(
        nodes=nodes,
        edges=edges,
        conditional_edges=conditional_edges,
        selector_edges=selector_edges,
        repeat=repeat,
        max_repeat=max_repeat,
        discussion=discussion,
    )

    # Validate and build indexes
    _validate_graph(schedule)
    schedule.build_indexes()

    return schedule


def _validate_graph(schedule: Schedule) -> None:
    """Validate the graph: check references, detect unreachable nodes."""
    id_set = {n.node_id for n in schedule.nodes}

    # Check for duplicate IDs
    if len(id_set) != len(schedule.nodes):
        seen = set()
        for n in schedule.nodes:
            if n.node_id in seen:
                raise ValueError(f"Duplicate node ID: '{n.node_id}'")
            seen.add(n.node_id)

    # Valid targets include all node IDs + END
    valid_targets = id_set | {END}

    # Validate fixed edges
    for e in schedule.edges:
        if e.source not in id_set:
            raise ValueError(f"Edge source '{e.source}' is not a valid node ID")
        if e.target not in valid_targets:
            raise ValueError(f"Edge target '{e.target}' is not a valid node ID")

    # Validate conditional edges
    for ce in schedule.conditional_edges:
        if ce.source not in id_set:
            raise ValueError(f"ConditionalEdge source '{ce.source}' is not a valid node ID")
        if not ce.condition:
            raise ValueError(f"ConditionalEdge from '{ce.source}' has no condition")
        if not ce.then_target:
            raise ValueError(f"ConditionalEdge from '{ce.source}' has no 'then' target")
        if ce.then_target not in valid_targets:
            raise ValueError(f"ConditionalEdge then_target '{ce.then_target}' is not a valid node ID")
        if ce.else_target and ce.else_target not in valid_targets:
            raise ValueError(f"ConditionalEdge else_target '{ce.else_target}' is not a valid node ID")

    # Validate selector edges
    for se in schedule.selector_edges:
        if se.source not in id_set:
            raise ValueError(f"SelectorEdge source '{se.source}' is not a valid node ID")
        src_node = {n.node_id: n for n in schedule.nodes}.get(se.source)
        if src_node and not src_node.is_selector:
            raise ValueError(f"SelectorEdge source '{se.source}' must have selector: true")
        if not se.choices:
            raise ValueError(f"SelectorEdge from '{se.source}' has no choices")
        for choice_num, target in se.choices.items():
            if target not in valid_targets:
                raise ValueError(f"SelectorEdge choice {choice_num} target '{target}' is not a valid node ID")


def load_schedule_file(path: str) -> Schedule:
    """Load and parse a schedule from a YAML file path."""
    with open(path, "r", encoding="utf-8") as f:
        return parse_schedule(f.read())


def extract_expert_names(schedule: Schedule) -> list[str]:
    """Extract all unique expert names referenced in a schedule (preserving order).

    Scans EXPERT and PARALLEL nodes for expert_names.
    ALL and MANUAL nodes don't reference specific experts so are skipped.
    Returns a deduplicated list in order of first appearance.
    """
    seen: set[str] = set()
    result: list[str] = []
    for node in schedule.nodes:
        if node.step_type in (StepType.EXPERT, StepType.PARALLEL):
            for name in node.expert_names:
                if name not in seen:
                    seen.add(name)
                    result.append(name)
    return result


def collect_external_configs(schedule: Schedule) -> dict[str, dict]:
    """Collect all external agent configs from schedule nodes.

    Returns a dict mapping expert_name → {api_url, api_key?, model?}.
    If an expert appears in multiple nodes with different configs, the first one wins.
    """
    configs: dict[str, dict] = {}
    for node in schedule.nodes:
        for name, cfg in node.external_configs.items():
            if name not in configs:
                configs[name] = cfg
    return configs
