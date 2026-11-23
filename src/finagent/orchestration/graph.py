"""The explicit run graph, loaded from the signed-off spec/graph.yaml."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

SPEC_PATH = Path(__file__).resolve().parents[3] / "spec" / "graph.yaml"
SPEC: dict[str, Any] = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
NODES: tuple[str, ...] = tuple(SPEC["nodes"])
TERMINAL: frozenset[str] = frozenset(SPEC["terminal"])
PAUSE_NODES: frozenset[str] = frozenset(SPEC["pause_nodes"])
PAUSED = "paused"  # a node's signal meaning "stop here", which is not a graph edge
_EDGES = {(edge["from"], edge["when"]): edge["to"] for edge in SPEC["transitions"] if edge["from"] != "*"}
_WILDCARDS = {edge["when"]: edge["to"] for edge in SPEC["transitions"] if edge["from"] == "*"}


@dataclass
class RunState:
    """Everything a run needs to continue. It is JSON-serialisable so it can be checkpointed."""

    run_id: str
    signal: str | None = None
    node: str = "VALIDATE"
    case_id: str = ""
    case_dir: str = ""
    visited: list[str] = field(default_factory=list)
    request: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    invoice: dict[str, Any] | None = None
    vendor: dict[str, Any] | None = None
    po: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    authority: list[dict[str, Any]] = field(default_factory=list)
    unknowns: list[dict[str, Any]] = field(default_factory=list)
    retrieved: list[dict[str, Any]] = field(default_factory=list)
    policy_sections: list[dict[str, Any]] = field(default_factory=list)
    sections: dict[str, Any] = field(default_factory=dict)
    outcome: str | None = None
    primary_category: str | None = None
    owner: str | None = None
    confidence: str | None = None
    next_action: str | None = None
    dfa_role: str | None = None
    approval_id: str | None = None
    action: str | None = None
    block_context: str = "BEFORE_REQUEST"
    block_rule: str | None = None
    block_authorisation_id: str | None = None
    block_confirmed: bool = False
    pending_write: str | None = None
    refresh: bool = False
    tool_calls: int = 0
    corpus_hash: str = ""
    config_hash: str = ""
    prompt_hash: str | None = None
    model_id: str | None = None
    llm_response: dict[str, Any] | None = None
    rationale_source: str = "TEMPLATE"
    actions: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)

    @classmethod
    def from_json(cls, text: str) -> "RunState":
        return cls(**json.loads(text))


def next_node(node: str, state: RunState) -> str:
    """The next node for this signal. Anything the spec does not declare fails safe."""
    if node in TERMINAL:
        raise ValueError(f"{node} is terminal")
    if state.signal in _WILDCARDS:
        return _WILDCARDS[state.signal]
    return _EDGES.get((node, state.signal), "FAILED")
