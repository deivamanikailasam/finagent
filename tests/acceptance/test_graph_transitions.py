# ruff: noqa: I001
"""The implemented graph must match spec/graph.yaml exactly."""

from pathlib import Path

import pytest
import yaml

from finagent.orchestration import graph
from tests.acceptance.support_graph import state_where

ROOT = Path(__file__).resolve().parents[2]
SPEC = yaml.safe_load((ROOT / "spec/graph.yaml").read_text(encoding="utf-8"))
EDGES = [edge for edge in SPEC["transitions"] if edge["from"] != "*"]
WILDCARDS = [edge for edge in SPEC["transitions"] if edge["from"] == "*"]
NON_TERMINAL = [node for node in SPEC["nodes"] if node not in SPEC["terminal"]]


def test_nodes_match_the_spec():
    assert set(graph.NODES) == set(SPEC["nodes"])
    assert len(graph.NODES) == 18


@pytest.mark.parametrize("edge", EDGES, ids=[f"{e['from']}:{e['when']}" for e in EDGES])
def test_every_transition(edge):
    assert graph.next_node(edge["from"], state_where(edge["from"], edge["when"])) == edge["to"]


@pytest.mark.parametrize("node", NON_TERMINAL)
@pytest.mark.parametrize("when", [edge["when"] for edge in WILDCARDS])
def test_wildcards_fail_from_every_non_terminal_node(node, when):
    assert graph.next_node(node, state_where(node, when)) == "FAILED"


@pytest.mark.parametrize("node", NON_TERMINAL)
def test_an_undeclared_signal_fails_safe(node):
    assert graph.next_node(node, state_where(node, "not_a_declared_signal")) == "FAILED"


@pytest.mark.parametrize("node", SPEC["terminal"])
def test_terminal_nodes_have_no_exit(node):
    with pytest.raises(ValueError):
        graph.next_node(node, state_where(node, "done"))


def test_fetch_po_is_only_entered_with_a_po_reference():
    entries = [(edge["from"], edge["when"]) for edge in EDGES if edge["to"] == "FETCH_PO"]
    assert entries == [("CHECK_HISTORY", "has_po_ref")]


def test_ineligible_approver_stays_at_the_pause():
    state = state_where("REQUEST_APPROVAL", "approver_not_eligible")
    assert graph.next_node("REQUEST_APPROVAL", state) == "REQUEST_APPROVAL"


def test_submit_is_only_entered_after_a_decision_path():
    assert {edge["from"] for edge in EDGES if edge["to"] == "SUBMIT"} == {
        "REQUEST_APPROVAL",
        "REVALIDATE",
        "SUBMIT_UNKNOWN",
    }
