from pathlib import Path

import pytest

from finagent.eval.retrieval_eval import (
    EvalReport,
    QueryResult,
    RuleResult,
    invariant_counts,
    load_case,
    reciprocal_rank,
    render_report,
    score_query,
    trap_above_target,
    write_section,
)
from finagent.tools.schemas import RetrievalItem, RetrieveFinanceDocumentsOutput

ROOT = Path(__file__).resolve().parents[2]


def item(doc_id="FIN-POL-003", trust_class="AUTHORITATIVE"):
    return RetrievalItem(
        doc_id=doc_id,
        doc_type="POLICY",
        version="1.0",
        section="s1",
        score=1.0,
        lane="authoritative",
        trust_class=trust_class,
        citation=f"{doc_id}#s1",
        text="text",
    )


def output(*items):
    return RetrieveFinanceDocumentsOutput(
        lanes={"authoritative": items, "case_evidence": (), "non_authoritative": ()},
        abstained={"authoritative": False, "case_evidence": True, "non_authoritative": True},
    )


@pytest.mark.parametrize(
    ("returned", "expected"),
    [(["a", "b"], 1.0), (["x", "a"], 0.5), (["x", "y", "b"], 1 / 3), (["x"], 0.0), ([], 0.0)],
)
def test_reciprocal_rank(returned, expected):
    assert reciprocal_rank(returned, ["a", "b"]) == pytest.approx(expected)


def test_hit_counts_only_the_top_five():
    inside = score_query("Q", "s", ["t"], ["1", "2", "3", "4", "t"], False)
    outside = score_query("Q", "s", ["t"], ["1", "2", "3", "4", "5", "t"], False)
    assert (inside.hit, inside.reciprocal_rank) == (True, 0.2)
    assert (outside.hit, outside.reciprocal_rank) == (False, 0.0)


def test_answerable_query_that_abstains_is_a_false_abstain():
    result = score_query("Q", "s", ["t"], [], True)
    assert (result.hit, result.false_abstain, result.false_answer) == (False, True, False)


def test_no_answer_query_that_abstains_is_correct():
    result = score_query("Q", "s", ["none"], [], True)
    assert (result.hit, result.reciprocal_rank, result.false_abstain, result.false_answer) == (
        None,
        None,
        False,
        False,
    )


def test_no_answer_query_that_returns_results_is_a_false_answer():
    assert score_query("Q", "s", ["none"], ["x"], False).false_answer is True


@pytest.mark.parametrize(
    ("ranking", "expected"),
    [
        (["TRAP#s0", "t"], True),
        (["t", "TRAP#s0"], False),
        (["t"], False),
        (["TRAP#s0"], True),
        ([], False),
    ],
)
def test_trap_above_target(ranking, expected):
    assert trap_above_target(ranking, "TRAP", ["t"]) is expected


def test_trap_match_does_not_confuse_a_similar_document_id():
    assert trap_above_target(["FIN-POL-003#s2", "t"], "FIN-POL-003-OLD", ["t"]) is False


def test_invariants_are_zero_for_clean_results():
    assert invariant_counts([output(item())]) == (0, 0)


def test_invariants_count_stale_and_non_authoritative_items():
    bad = output(item("FIN-POL-003-OLD", "SUPERSEDED"), item("ADV-001", "EXTERNAL_UNVERIFIED"))
    assert invariant_counts([bad, output(item())]) == (2, 2)


def test_load_case_reads_fixture_files_by_path():
    case = load_case(ROOT / "fixtures/cases/FIN-001")
    assert case.request.case_id == "FIN-001"
    assert case.po.po_ref == "PO-4471"
    assert case.as_of.date().isoformat() == "2026-09-15"


def test_report_lists_misses_and_every_query():
    report = EvalReport(
        rules=(
            RuleResult("R1", "dfa_tier", "A#s1", 1),
            RuleResult("R2", "fx", "B#s2", None, ("B#s3",)),
        ),
        queries=(
            QueryResult("G1", "templated", ("A#s1",), ("A#s1",), True, 1.0, False, False, False),
            QueryResult("G2", "no_answer", ("none",), (), None, None, True, False, False),
        ),
        stale_in_authoritative=0,
        non_authoritative_cited=0,
    )
    text = render_report(report)
    assert "1/2 (50.0%)" in text
    assert "R2 (fx): B#s2 not in the top 5" in text
    assert "R2: B#s3" in text
    assert "| G1 |" in text and "| G2 |" in text
    assert "Stale in authoritative: 0" in text


def test_write_section_creates_the_file(tmp_path):
    path = tmp_path / "docs" / "EVAL.md"
    write_section(path, "## Retrieval\n\nfirst\n")
    assert path.read_text() == "# Evaluation results\n\n## Retrieval\n\nfirst\n"


def test_write_section_replaces_only_its_section(tmp_path):
    path = tmp_path / "EVAL.md"
    path.write_text("# E\n\n## Retrieval\n\nold\n\n## Scenarios\n\nkeep\n")
    write_section(path, "## Retrieval\n\nnew\n\n")
    assert path.read_text() == "# E\n\n## Retrieval\n\nnew\n\n## Scenarios\n\nkeep\n"


def test_write_section_appends_when_absent(tmp_path):
    path = tmp_path / "EVAL.md"
    path.write_text("# E\n\n## Scenarios\n\nkeep\n")
    write_section(path, "## Retrieval\n\nnew\n")
    assert path.read_text() == "# E\n\n## Scenarios\n\nkeep\n\n## Retrieval\n\nnew\n"
