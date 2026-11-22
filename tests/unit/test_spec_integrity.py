"""Checks the signed-off specs. Reads spec/ from its final path, so it fails until the drafts are moved in."""

import hashlib
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from finagent.rag.section_hash import section_hash
from finagent.tools.declarations import ToolDeclaration, load_tool_declarations

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "corpus"
EXPECTED_VECTORS = 45


@lru_cache
def load(relative: str):
    return yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))


def rules():
    return load("spec/policy_rules.yaml")["rules"]


def graph():
    return load("spec/graph.yaml")


def permissions():
    return load("spec/submit_permissions.yaml")


def registry():
    return load("config/registry.yaml")["documents"]


def corpus_text(doc_id: str) -> str:
    entry = next(item for item in registry() if item["doc_id"] == doc_id)
    return (CORPUS / entry["file"]).read_text(encoding="utf-8")


def walk(value):
    yield value
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def test_exactly_31_rules_with_unique_ids():
    ids = [rule["id"] for rule in rules()]
    assert len(ids) == 31
    assert len(set(ids)) == 31


def test_exact_vector_count_with_unique_names():
    names = [vector["name"] for rule in rules() for vector in rule["vectors"]]
    assert len(names) == EXPECTED_VECTORS
    assert len(set(names)) == EXPECTED_VECTORS


def test_every_rule_has_a_vector():
    assert [rule["id"] for rule in rules() if not rule["vectors"]] == []


def test_every_vector_expects_an_outcome_or_error():
    for rule in rules():
        for vector in rule["vectors"]:
            assert bool(vector["expect"].get("outcome")) != bool(vector["expect"].get("error")), vector[
                "name"
            ]


def test_vectors_contain_no_float_money():
    for rule in rules():
        for vector in rule["vectors"]:
            assert not any(isinstance(item, float) for item in walk(vector["overrides"])), vector["name"]


def test_every_rule_hash_matches_the_corpus():
    for rule in rules():
        for source in [rule["source"], *rule["source"].get("additional_sections", [])]:
            doc_id = source.get("doc", rule["source"]["doc"])
            number = int(str(source["section"]).removeprefix("s"))
            assert source["section_hash"] == section_hash(corpus_text(doc_id), number), rule["id"]


def test_registry_covers_every_corpus_file_with_matching_sha256():
    entries = registry()
    assert len(entries) == 15
    assert {entry["file"] for entry in entries} == {path.name for path in CORPUS.glob("*.md")}
    for entry in entries:
        lf_bytes = (CORPUS / entry["file"]).read_bytes().replace(b"\r\n", b"\n")
        assert entry["sha256"] == hashlib.sha256(lf_bytes).hexdigest(), entry["file"]


def test_every_graph_node_appears_in_a_transition():
    spec = graph()
    seen = {edge["from"] for edge in spec["transitions"]} | {edge["to"] for edge in spec["transitions"]}
    assert set(spec["nodes"]) <= seen
    assert seen - {"*"} <= set(spec["nodes"])


def test_graph_has_18_nodes_and_terminals_have_no_exit():
    spec = graph()
    assert len(spec["nodes"]) == 18
    assert not {edge["from"] for edge in spec["transitions"]} & set(spec["terminal"])


def test_place_block_unknown_result_goes_to_reconciliation():
    edges = graph()["transitions"]
    assert any(
        edge["from"] == "PLACE_BLOCK" and edge["when"] == "unknown_result" and edge["to"] == "SUBMIT_UNKNOWN"
        for edge in edges
    )


def test_fetch_po_is_only_reached_with_a_po_reference():
    into_fetch_po = [edge for edge in graph()["transitions"] if edge["to"] == "FETCH_PO"]
    assert [(edge["from"], edge["when"]) for edge in into_fetch_po] == [("CHECK_HISTORY", "has_po_ref")]


def test_ineligible_approver_stays_paused():
    assert {
        "from": "REQUEST_APPROVAL",
        "when": "approver_not_eligible",
        "to": "REQUEST_APPROVAL",
    } in graph()["transitions"]


def test_every_permission_action_is_covered():
    spec = permissions()
    assert spec["default"] == "DENY"
    assert set(spec["tools"]["place_payment_block"]["actions"]) == {"BLOCK"}
    assert set(spec["tools"]["submit_finance_decision"]["actions"]) == {"POST", "REJECT", "HOLD", "ESCALATE"}
    for tool in spec["tools"].values():
        for action, rule in tool["actions"].items():
            assert "action" in rule["bound_to"], action


def test_system_failsafe_only_authorises_the_block():
    tools = permissions()["tools"]
    assert tools["place_payment_block"]["actions"]["BLOCK"]["requires"] == "SYSTEM_FAILSAFE"
    for rule in tools["submit_finance_decision"]["actions"].values():
        assert rule["requires"] == "HUMAN_APPROVAL"
        assert rule["approval_state"] == "APPROVED"


def test_block_reconciliation_never_skips_approval():
    edges = {(e["from"], e["when"]): e["to"] for e in graph()["transitions"]}
    assert edges[("SUBMIT_UNKNOWN", "block_row_found")] == "PLACE_BLOCK"
    assert edges[("SUBMIT_UNKNOWN", "block_no_row_retry")] == "PLACE_BLOCK"


def test_blocked_run_can_never_post():
    spec = permissions()
    assert spec["tools"]["submit_finance_decision"]["actions"]["POST"]["blocked_run"] == "DENY"
    assert {"tool": "submit_finance_decision", "action": "POST", "run_has_block": True} in spec["never"]
    assert graph()["approve_on_blocked_run"]["becomes"] == "HOLD_FOR_INFORMATION"


def test_unique_violation_is_a_reserved_block_rule_id():
    reserved = permissions()["tools"]["place_payment_block"]["reserved_rule_ids"]
    assert set(reserved) == {"APPROVER_REJECT", "REVALIDATION", "UNIQUE_VIOLATION"}


def test_tool_declarations_match_the_decision_log():
    config = load_tool_declarations(ROOT / "config/tools.yaml")
    assert len(config.tools) == 8
    for tool in config.tools:
        expected = (2.0, 3) if tool.access == "read" else (3.0, 1)
        assert (tool.timeout_s, tool.max_attempts) == expected, tool.name
    assert (config.budget.max_steps, config.budget.max_tool_calls) == (40, 20)


@pytest.mark.parametrize("overrides", [{"max_attempts": 2}, {"retry_on": ("timeout",)}])
def test_declarations_reject_a_retried_write_tool(overrides):
    fields = {
        "name": "submit_finance_decision",
        "purpose": "test",
        "access": "write",
        "timeout_s": 3.0,
        "max_attempts": 1,
        "impl": "simulated",
    }
    with pytest.raises(ValidationError, match="must have max_attempts 1"):
        ToolDeclaration(**{**fields, **overrides})


def test_section_hash_ignores_whitespace_and_line_endings():
    text = "---\nid: X\n---\n# T\n\n## 1. One\n\nalpha   beta\n\n## 2. Two\n\ngamma\n"
    assert section_hash(text, 1) == section_hash(text.replace("\n", "\r\n").replace("alpha   ", "alpha "), 1)
    assert section_hash(text, 1) == hashlib.sha256(b"## 1. One alpha beta").hexdigest()
    assert section_hash(text, 0) == hashlib.sha256(b"# T ## 1. One alpha beta ## 2. Two gamma").hexdigest()


def test_section_hash_rejects_a_missing_section():
    with pytest.raises(ValueError):
        section_hash("# T\n\n## 1. One\n", 2)
