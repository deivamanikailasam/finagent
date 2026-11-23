"""Runs each signed-off case in an isolated workspace and compares every expectation."""

import json
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from finagent.config.clock import SYDNEY, FixedClock
from finagent.controls.decision import Evaluation, evaluate
from finagent.domain.case import Case
from finagent.llm.fake import FakeLLM
from finagent.orchestration.checkpoint import rehydrate
from finagent.orchestration.graph import RunState
from finagent.orchestration.runner import ROOT, build_deps
from finagent.persistence.db import connect
from finagent.persistence.repos import ApprovalRecord, ApprovalRepo, CheckpointRepo
from finagent.service.run_service import (
    CASES_DIR,
    DecisionCommand,
    DecisionResponse,
    RunDetail,
    RunService,
    StartCommand,
)

EXPECTATIONS_PATH = ROOT / "tests" / "scenarios" / "expectations.yaml"
AS_OF = datetime(2026, 9, 15, 9, 0, tzinfo=SYDNEY)
NO_CHECK = "<no check for this key>"
_EXACT_KEYS = (
    "outcome",
    "primary_category",
    "owner",
    "run_status_before_decision",
    "rows_before_decision",
    "run_status_after_decision",
    "rows_after_decision",
)
_HANDLED_KEYS = frozenset(
    {
        *_EXACT_KEYS,
        "decision",
        "must_cite",
        "must_not_cite",
        "unknowns",
        "assumptions",
        "forbidden_actions",
        "extra",
    }
)
_NEGATED_EXTRAS = frozenset({"never_outcome"})  # the observed value must differ from the expected one
_KEY_EVENTS = frozenset(
    {
        "approval_requested",
        "approval_decided",
        "approval_revalidated",
        "block_placed",
        "decision_submitted",
        "llm_call",
        "security_event",
        "receipt_evidence_query",
    }
)
_LONG_DIGITS = re.compile(r"\b[0-9]{6,}\b")
_MIN_EVIDENCE_TEXT = 25


@dataclass(frozen=True)
class Assertion:
    key: str
    expected: Any
    actual: Any
    passed: bool


@dataclass(frozen=True)
class Observation:
    outcome: str | None
    primary_category: str | None
    owner: str | None
    run_status_before_decision: str
    rows_before_decision: dict[str, Any]
    run_status_after_decision: str
    rows_after_decision: dict[str, Any]
    citations: frozenset[str]
    unknowns: tuple[str, ...]
    assumptions: tuple[str, ...]
    actions: tuple[str, ...]
    extra: Mapping[str, Any] = field(default_factory=dict)
    artifacts: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    assertions: tuple[Assertion, ...]
    artifacts: Mapping[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return bool(self.assertions) and all(item.passed for item in self.assertions)


def load_expectations(path: Path = EXPECTATIONS_PATH) -> dict[str, dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]


def is_cited(wanted: str, citations: frozenset[str]) -> bool:
    """An entry with # or : is exact; a bare document ID matches any section of that document."""
    if "#" in wanted or ":" in wanted:
        return wanted in citations
    return any(item == wanted or item.startswith(f"{wanted}#") for item in citations)


def compare(expected: Mapping[str, Any], observed: Observation) -> tuple[Assertion, ...]:
    results: list[Assertion] = []

    def check(key: str, want: Any, got: Any, passed: bool | None = None) -> None:
        results.append(Assertion(key, want, got, want == got if passed is None else passed))

    for key in _EXACT_KEYS:
        if key in expected:
            check(key, expected[key], getattr(observed, key))
    for wanted in expected.get("must_cite", ()):
        found = is_cited(wanted, observed.citations)
        check(f"must_cite[{wanted}]", True, found)
    for wanted in expected.get("must_not_cite", ()):
        found = is_cited(wanted, observed.citations)
        check(f"must_not_cite[{wanted}]", False, found)
    if "unknowns" in expected:
        check("unknowns", sorted(expected["unknowns"]), sorted(observed.unknowns))
    if "assumptions" in expected:
        check("assumptions", list(expected["assumptions"]), list(observed.assumptions))
    for action in expected.get("forbidden_actions", ()):
        check(f"forbidden_actions[{action}]", False, action in observed.actions)
    for key, want in expected.get("extra", {}).items():
        got = observed.extra.get(key, NO_CHECK)
        passed = got != NO_CHECK and (got != want if key in _NEGATED_EXTRAS else got == want)
        check(f"extra.{key}", want, got, passed)
    for key in sorted(set(expected) - _HANDLED_KEYS):  # a new expectation must never pass unchecked
        check(key, expected[key], NO_CHECK, False)
    return tuple(results)


def _rows(home: Path) -> dict[str, Any]:
    conn = connect(home / "erp_sandbox.db")
    try:
        block = conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        ledger = dict(conn.execute("SELECT action, COUNT(*) FROM ledger GROUP BY action").fetchall())
    finally:
        conn.close()
    return {"block": block, "ledger": ledger}


def _decide(service: RunService, run_id: str, decision: Mapping[str, Any]) -> DecisionResponse:
    approver, action = decision["approver"], decision["action"]
    if action == "approve":
        return service.approve(DecisionCommand(run_id=run_id, approver=approver))
    reason = decision.get("reason") or "scripted scenario rejection"
    return service.reject(DecisionCommand(run_id=run_id, approver=approver, reason=reason))


def _text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for item in value.values() for text in _text_values(item)]
    if isinstance(value, list | tuple):
        return [text for item in value for text in _text_values(item)]
    return []


@dataclass
class _Context:
    case_id: str
    home: Path
    service: RunService
    llm: FakeLLM
    detail: RunDetail
    state: RunState
    approval: ApprovalRecord | None
    responses: tuple[DecisionResponse, ...]
    citations: frozenset[str]

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.detail.audit_events or []

    @property
    def prompts(self) -> list[str]:
        return [request.prompt for request in self.llm.requests]

    def replayed(self) -> Evaluation:
        """The recorded inputs evaluated again, for fields FinalResult does not carry."""
        state = self.state
        deps = build_deps(Path(state.case_dir), self.home, FixedClock(AS_OF), self.llm, request=state.request)
        try:
            rehydrate(state, deps.retriever, AS_OF)
        finally:
            deps.conn.close()
        case = Case.model_validate(
            {
                "request": state.request,
                "invoice": state.invoice,
                "vendor": state.vendor,
                "po": state.po,
                "history": state.history,
                "authority": state.authority,
                "evidence": state.evidence,
                "as_of": AS_OF,
                "unknowns": state.unknowns,
                "retrieved_chunks": state.retrieved,
            }
        )
        return evaluate(case, FixedClock(AS_OF))


def _gross(ctx: _Context, _want: Any) -> Any:
    result = ctx.detail.result
    return next((str(item.result) for item in result.calculations if item.id == "CALC-GROSS"), None)


def _adv_neutral(ctx: _Context, want: Any) -> Any:
    return want if not is_cited("ADV-001", ctx.citations) else "cited"


def _block_first(ctx: _Context, _want: Any) -> bool:
    names = [event["event"] for event in ctx.events]
    return (
        "block_placed" in names
        and "approval_requested" in names
        and (names.index("block_placed") < names.index("approval_requested"))
    )


def _indicators_with_evidence(ctx: _Context, _want: Any) -> bool:
    indicators = ctx.replayed().indicators
    return len({item.indicator for item in indicators}) >= 2 and all(item.doc_id for item in indicators)


def _injection_neutral(ctx: _Context, _want: Any) -> bool:
    with tempfile.TemporaryDirectory(prefix="finagent-eval-") as directory:
        llm = FakeLLM("injection_obeying")
        command = StartCommand(case_dir=CASES_DIR / ctx.case_id, as_of=AS_OF)
        run = RunService(Path(directory), lambda: llm).start(command)
    source = run.result.recommendation.rationale_source if run.result else None
    return run.outcome == ctx.state.outcome and source == "TEMPLATE"


def _attachment_in_prompt(ctx: _Context, _want: Any) -> bool:
    case_dir = CASES_DIR / ctx.case_id
    texts = _text_values(json.loads((case_dir / "evidence.json").read_text(encoding="utf-8")))
    for path in case_dir.iterdir():
        if path.suffix != ".json":
            texts += path.read_text(encoding="utf-8").splitlines()
    long_texts = [text.strip() for text in texts if len(text.strip()) >= _MIN_EVIDENCE_TEXT]
    return any(text in prompt for text in long_texts for prompt in ctx.prompts)


def _bank_masked(ctx: _Context, _want: Any) -> bool:
    shown = [ctx.detail.result.model_dump_json(), *ctx.prompts]
    return not any(_LONG_DIGITS.search(text) for text in shown)


def _reprocessing_escalated(ctx: _Context, _want: Any) -> bool:
    command = StartCommand(case_dir=CASES_DIR / ctx.case_id, as_of=AS_OF)
    return ctx.service.start(command).outcome == "ESCALATE_CONTROL_REVIEW"


def _po_attempts(ctx: _Context, _want: Any) -> int:
    return sum(1 for e in ctx.events if e.get("tool") == "get_purchase_order" and "attempt" in e)


def _receipt_events(ctx: _Context) -> list[dict[str, Any]]:
    return [event for event in ctx.events if event["event"] == "receipt_evidence_query"]


_EXTRA_CHECKS: dict[str, Callable[[_Context, Any], Any]] = {
    "dfa_role": lambda ctx, _want: ctx.state.dfa_role,
    "gross": _gross,
    "adv_001_if_retrieved": _adv_neutral,
    "block_placed_before_approval": _block_first,
    "notify_supplier": lambda ctx, _want: ctx.replayed().notify_supplier,
    "routed_to": lambda ctx, _want: ctx.detail.result.recommendation.owner,
    "next_action_contains": lambda ctx, want: [
        text for text in want if text in ctx.detail.result.recommendation.next_action
    ],
    "indicators_listed_with_evidence": _indicators_with_evidence,
    "outcome_unchanged_when_model_obeys_injection": _injection_neutral,
    "attachment_text_in_prompt": _attachment_in_prompt,
    "bank_details_masked": _bank_masked,
    "reprocessing_stays_escalated": _reprocessing_escalated,
    "po_attempts": _po_attempts,
    "receipt_query_abstained": lambda ctx, _want: any(
        event["outcome"] == "abstained" for event in _receipt_events(ctx)
    ),
    "receipt_query_audited": lambda ctx, _want: bool(_receipt_events(ctx)),
    "approval_request_action": lambda ctx, _want: ctx.approval.action if ctx.approval else None,
    "never_outcome": lambda ctx, _want: ctx.state.outcome,
    "identical_responses_on_replay": lambda ctx, _want: (
        len(ctx.responses) == 2 and ctx.responses[0] == ctx.responses[1]
    ),
}


def _approval_artifact(record: ApprovalRecord | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "approval_id": record.approval_id,
        "action": record.action,
        "outcome": record.outcome,
        "status": record.status,
        "vendor_id": record.vendor_id,
        "vendor_name": record.vendor_name,
        "amount": str(record.amount),
        "currency": record.currency,
        "exceptions": list(record.exceptions),
        "citations": list(record.citations),
        "approver_id": record.approver_id,
        "role": record.role,
    }


def observe_case(case_id: str, expected: Mapping[str, Any]) -> Observation:
    """Run one case to its pause, apply the scripted decision and run to its end."""
    with tempfile.TemporaryDirectory(prefix="finagent-eval-") as directory:
        home = Path(directory)
        llm = FakeLLM("good")
        service = RunService(home, lambda: llm)
        paused = service.start(StartCommand(case_dir=CASES_DIR / case_id, as_of=AS_OF))
        rows_before = _rows(home)
        decision = expected["decision"]
        responses = [_decide(service, paused.run_id, decision)]
        if decision.get("repeat"):
            responses.append(_decide(service, paused.run_id, decision))
        rows_after = _rows(home)
        detail = service.get(paused.run_id)
        conn = connect(home / "finagent.db")
        try:
            state = RunState.from_json(CheckpointRepo(conn).latest(paused.run_id)[2])
            approval = ApprovalRepo(conn).get(state.approval_id) if state.approval_id else None
        finally:
            conn.close()
        result = detail.result
        cited_items = (*result.findings, *result.exceptions, result.recommendation)
        citations = frozenset(citation for item in cited_items for citation in item.citations)
        context = _Context(case_id, home, service, llm, detail, state, approval, tuple(responses), citations)
        extra = {
            key: _EXTRA_CHECKS[key](context, want) if key in _EXTRA_CHECKS else NO_CHECK
            for key, want in expected.get("extra", {}).items()
        }
        artifacts = {
            "request": state.request,
            "key_audit_events": [e for e in detail.audit_events if e["event"] in _KEY_EVENTS],
            "final_result": result.model_dump(mode="json"),
            "approval_request": _approval_artifact(approval),
        }
        recommendation = result.recommendation
        return Observation(
            outcome=str(recommendation.outcome),
            primary_category=str(recommendation.primary_category)
            if recommendation.primary_category
            else None,
            owner=recommendation.owner,
            run_status_before_decision=paused.status,
            rows_before_decision=rows_before,
            run_status_after_decision=detail.status,
            rows_after_decision=rows_after,
            citations=citations,
            unknowns=tuple(item.source for item in result.unknowns),
            assumptions=tuple(item.statement for item in result.assumptions),
            actions=tuple(item.action for item in result.actions_taken),
            extra=extra,
            artifacts=artifacts,
        )


def run_case(
    case_id: str,
    expected: Mapping[str, Any],
    observe: Callable[[str, Mapping[str, Any]], Observation] = observe_case,
) -> CaseResult:
    try:
        observed = observe(case_id, expected)
    except Exception as exc:  # a crash in one case is a reported failure, never a pass
        failure = Assertion("execution", "completed", type(exc).__name__, False)
        return CaseResult(case_id, (failure,))
    return CaseResult(case_id, compare(expected, observed), observed.artifacts)


def run_scenarios(
    expectations: Mapping[str, Mapping[str, Any]] | None = None,
    observe: Callable[[str, Mapping[str, Any]], Observation] = observe_case,
) -> tuple[CaseResult, ...]:
    cases = load_expectations() if expectations is None else expectations
    return tuple(run_case(case_id, expected, observe) for case_id, expected in cases.items())
