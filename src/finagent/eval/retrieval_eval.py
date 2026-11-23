"""Retrieval evaluation (plan 2.1): rule-source recall@5, the golden set and trust invariants.

Settings are fixed in advance. Run with: uv run python -m finagent.eval.retrieval_eval
"""

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from finagent.config.clock import SYDNEY
from finagent.domain.case import Case
from finagent.domain.enums import TrustClass
from finagent.rag.retrieve import LANE_K, Retriever, default_retriever
from finagent.rag.templates import TEMPLATES
from finagent.rag.tokenizer import tokenize
from finagent.tools.schemas import RetrieveFinanceDocumentsOutput

TOP_K = 5
NO_ANSWER = "none"
ROLE = "agent"
USE_PREFIX = True
NON_AUTHORITATIVE_DOCS = frozenset({"FIN-POL-003-OLD", "ADV-001", "ADV-002"})
TRAP_DOCS = {
    "trap_superseded_dfa": "FIN-POL-003-OLD",
    "trap_injection_bank_change": "ADV-001",
    "trap_aud_75_freight": "ADV-002",
}
BOUNDARY_DATES = (datetime(2026, 7, 31, 9, 0, tzinfo=SYDNEY), datetime(2026, 8, 1, 9, 0, tzinfo=SYDNEY))
NOT_EVALUATED = (
    "BM25 k1/b, stemming and stopwords",
    "the prefix on/off sweep",
    "top-k",
    "the repair count",
    "retry parameters (fault tests cover behaviour)",
    "prompt variants",
    "Sonnet 5.5",
    "synonym expansion",
)


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    template: str
    citation: str
    rank: int | None
    additional_missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    slice: str
    relevant: tuple[str, ...]
    returned: tuple[str, ...]
    hit: bool | None
    reciprocal_rank: float | None
    abstained: bool
    false_abstain: bool
    false_answer: bool
    trap_above_target: bool | None = None
    rank_before_abstention: int | None = None


@dataclass(frozen=True)
class EvalReport:
    rules: tuple[RuleResult, ...]
    queries: tuple[QueryResult, ...]
    stale_in_authoritative: int
    non_authoritative_cited: int


def reciprocal_rank(returned: Sequence[str], relevant: Iterable[str]) -> float:
    targets = set(relevant)
    return next((1 / rank for rank, item in enumerate(returned, 1) if item in targets), 0.0)


def score_query(
    query_id: str,
    slice_name: str,
    relevant: Sequence[str],
    returned: Sequence[str],
    abstained: bool,
    trap_above: bool | None = None,
    rank_before_abstention: int | None = None,
) -> QueryResult:
    """Score one query. An unanswerable query has no hit or rank, only a false-answer check."""
    answerable = list(relevant) != [NO_ANSWER]
    top = list(returned[:TOP_K])
    rank = reciprocal_rank(top, relevant) if answerable else None
    return QueryResult(
        query_id=query_id,
        slice=slice_name,
        relevant=tuple(relevant),
        returned=tuple(top),
        hit=bool(rank) if answerable else None,
        reciprocal_rank=rank,
        abstained=abstained,
        false_abstain=answerable and abstained,
        false_answer=not answerable and not abstained,
        trap_above_target=trap_above,
        rank_before_abstention=rank_before_abstention,
    )


def trap_above_target(ranking: Sequence[str], trap_doc: str, relevant: Iterable[str]) -> bool:
    """True when the trap document outranks every relevant section in an unfiltered ranking."""
    targets = set(relevant)
    trap = next((i for i, item in enumerate(ranking) if item.startswith(f"{trap_doc}#")), None)
    target = next((i for i, item in enumerate(ranking) if item in targets), None)
    if trap is None:
        return False
    return target is None or trap < target


def invariant_counts(
    outputs: Iterable[tuple[RetrieveFinanceDocumentsOutput, datetime]],
    in_force: Callable[[str, datetime], bool],
) -> tuple[int, int]:
    """Count (items not in force at their as_of, non-authoritative documents) in the authoritative lane."""
    stale = cited = 0
    for output, as_of in outputs:
        for item in output.lanes["authoritative"]:
            stale += item.trust_class != TrustClass.AUTHORITATIVE or not in_force(item.doc_id, as_of)
            cited += item.doc_id in NON_AUTHORITATIVE_DOCS
    return stale, cited


def load_case(directory: Path) -> Case:
    """Read a case by path; runtime code never imports the fixtures package."""

    def read(name: str) -> Any:
        return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))

    meta = read("meta")
    records = {
        name: read(name)
        for name in ("request", "invoice", "vendor", "po", "history", "authority", "evidence")
    }
    return Case.model_validate(
        {**records, "as_of": meta["as_of"], "authority_source": meta["authority_source"]}
    )


def _authoritative(output: RetrieveFinanceDocumentsOutput) -> list[str]:
    return [item.citation for item in output.lanes["authoritative"]]


def _unfiltered_ranking(retriever: Retriever, query: str) -> list[str]:
    scored = retriever.index.retrieve(tokenize(query), k=len(retriever.index.chunks))
    ordered = sorted(
        ((chunk.chunk_id, score) for chunk, score in scored if score > 0), key=lambda x: (-x[1], x[0])
    )
    return [chunk_id for chunk_id, _ in ordered]


def evaluate(
    retriever: Retriever, rules: list[dict[str, Any]], queries: list[dict[str, Any]], case: Case
) -> EvalReport:
    outputs: dict[tuple[str, datetime], RetrieveFinanceDocumentsOutput] = {}

    def search(query: str, as_of: datetime = case.as_of) -> RetrieveFinanceDocumentsOutput:
        if (query, as_of) not in outputs:
            outputs[(query, as_of)] = retriever.retrieve_finance_documents(query, None, ROLE, as_of)
        return outputs[(query, as_of)]

    rule_results = []
    for rule in rules:
        source = rule["source"]
        returned = _authoritative(search(TEMPLATES[rule["template"]](case)))[:TOP_K]
        citation = f"{source['doc']}#{source['section']}"
        rank = returned.index(citation) + 1 if citation in returned else None
        extra = [
            f"{item.get('doc', source['doc'])}#{item['section']}"
            for item in source.get("additional_sections", [])
        ]
        missing = tuple(item for item in extra if item not in returned)
        rule_results.append(RuleResult(rule["id"], rule["template"], citation, rank, missing))
    query_results = []
    for query in queries:
        output = search(query["query"])
        trap = TRAP_DOCS.get(query["slice"])
        above = None
        if trap is not None:
            above = trap_above_target(_unfiltered_ranking(retriever, query["query"]), trap, query["relevant"])
        ranking = retriever.lane_ranking(query["query"], None, ROLE, case.as_of, "authoritative")
        best = next((i for i, item in enumerate(ranking, 1) if item in query["relevant"]), None)
        query_results.append(
            score_query(
                query["id"],
                query["slice"],
                query["relevant"],
                _authoritative(output),
                output.abstained["authoritative"],
                above,
                best,
            )
        )
    # The stale check also runs on both sides of the FIN-POL-003 effective date.
    for as_of in BOUNDARY_DATES:
        for query in {*(fn(case) for fn in TEMPLATES.values()), *(item["query"] for item in queries)}:
            search(query, as_of)
    stale, cited = invariant_counts(
        ((output, as_of) for (_, as_of), output in outputs.items()), retriever.is_in_force
    )
    return EvalReport(tuple(rule_results), tuple(query_results), stale, cited)


def _flag(value: bool | None) -> str:
    return "n/a" if value is None else ("yes" if value else "no")


def render_report(report: EvalReport) -> str:
    found = sum(rule.rank is not None for rule in report.rules)
    strict = sum(rule.rank is not None and not rule.additional_missing for rule in report.rules)
    lines = [
        "## Retrieval",
        "",
        f"Settings fixed before measuring: heading prefix {'on' if USE_PREFIX else 'off'}, top-k "
        f"{LANE_K['authoritative']} / {LANE_K['case_evidence']} / {LANE_K['non_authoritative']} per lane, "
        "abstain on an empty lane, a top score of 0 or less, or matched-IDF coverage below 0.5.",
        "",
        "Golden labels were proposed by Claude from the policy text, without running retrieval, "
        "accepted by the owner, and committed before this evaluation.",
        "",
        "This run follows the independent review fixes to scoring scope, tokenisation and tie-breaking. "
        "The first run, before those fixes, measured rule-source recall 26/31, with G3 and G4 as false "
        "abstentions. Templates, thresholds and labels were not changed between runs.",
        "",
        f"**Rule-source recall@{TOP_K}: {found}/{len(report.rules)} ({found / len(report.rules):.1%})**",
        "",
        f"Requiring every pinned section of a rule: {strict}/{len(report.rules)} "
        f"({strict / len(report.rules):.1%}).",
        "",
        "### Rule-source misses",
        "",
    ]
    misses = [rule for rule in report.rules if rule.rank is None]
    lines.extend(
        f"- {rule.rule_id} ({rule.template}): {rule.citation} not in the top {TOP_K}" for rule in misses
    )
    if not misses:
        lines.append("None.")
    extra = [rule for rule in report.rules if rule.additional_missing]
    lines.extend(["", "### Additional pinned sections not in the top 5 (information)", ""])
    lines.extend(f"- {rule.rule_id}: {', '.join(rule.additional_missing)}" for rule in extra)
    if not extra:
        lines.append("None.")
    lines.extend(
        [
            "",
            "### Golden set",
            "",
            "| Query | Slice | Relevant | Hit@5 | RR | Abstained | False abstain | False answer "
            "| Trap above target |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for query in report.queries:
        rank = "n/a" if query.reciprocal_rank is None else f"{query.reciprocal_rank:.2f}"
        lines.append(
            f"| {query.query_id} | {query.slice} | {', '.join(query.relevant)} | "
            f"{_flag(query.hit)} | {rank} | {_flag(query.abstained)} | "
            f"{_flag(query.false_abstain)} | {_flag(query.false_answer)} | "
            f"{_flag(query.trap_above_target)} |"
        )
    lines.extend(
        [
            "",
            "Trap above target is measured on the unfiltered ranking, for information only.",
            "",
            "### Golden misses",
            "",
        ]
    )
    golden_misses = [query for query in report.queries if query.hit is False or query.false_answer]
    for query in golden_misses:
        if query.abstained and query.rank_before_abstention is not None:
            detail = f"retrieved at rank {query.rank_before_abstention}, then suppressed by abstention"
        elif query.false_answer:
            detail = "answered although no authoritative section applies"
        else:
            detail = "relevant section not retrieved in the top 5"
        lines.append(f"- {query.query_id}: {detail}")
    if not golden_misses:
        lines.append("None.")
    lines.extend(
        [
            "",
            "### Invariants",
            "",
            "Checked at the case date and on both sides of the FIN-POL-003 effective date.",
            "",
            f"- Stale in authoritative: {report.stale_in_authoritative}",
            f"- Non-authoritative cited: {report.non_authoritative_cited}",
            "",
            "### Not evaluated",
            "",
            *[f"- {item}" for item in NOT_EVALUATED],
            "",
        ]
    )
    return "\n".join(lines)


def write_section(path: Path, section: str) -> None:
    """Replace the Retrieval section and keep every other section."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Evaluation results\n\n{section}", encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    start = text.find("## Retrieval")
    if start == -1:
        path.write_text(f"{text.rstrip()}\n\n{section}", encoding="utf-8")
        return
    following = text.find("\n## ", start + 1)
    tail = text[following + 1 :] if following != -1 else ""
    path.write_text(f"{text[:start]}{section}{tail}", encoding="utf-8")


def run_retrieval_eval(root: Path = Path(".")) -> EvalReport:
    rules = yaml.safe_load((root / "spec/policy_rules.yaml").read_text(encoding="utf-8"))["rules"]
    queries = yaml.safe_load((root / "tests/retrieval/golden.yaml").read_text(encoding="utf-8"))["queries"]
    case = load_case(root / "fixtures/cases/FIN-001")
    report = evaluate(default_retriever(root), rules, queries, case)
    write_section(root / "docs/EVAL_RESULTS.md", render_report(report))
    return report


if __name__ == "__main__":
    print(render_report(run_retrieval_eval()))
