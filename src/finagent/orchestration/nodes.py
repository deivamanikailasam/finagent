"""One function per graph node. Each reads and updates RunState and sets state.signal."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from finagent.approvals.service import ApprovalRequest
from finagent.controls.decision import OUTCOME_CITATION, evaluate
from finagent.controls.duplicates import normalise_invoice_number
from finagent.controls.rules import load_rules
from finagent.domain.case import Case
from finagent.domain.enums import Outcome
from finagent.domain.results import (
    Action,
    Calculation,
    ExceptionRecord,
    Fact,
    FinalResult,
    Inference,
    PolicyFinding,
    Recommendation,
    Unknown,
    next_review_date,
)
from finagent.llm.prompt import PromptChunk, PromptLine, RationaleInput, build_prompt, prompt_hash
from finagent.llm.router import Router
from finagent.llm.validator import store_raw
from finagent.obs.redaction import redact
from finagent.orchestration.graph import PAUSED, SPEC, RunState
from finagent.rag.templates import TEMPLATES
from finagent.tools.block_submit import (
    Denied,
    DuplicateRisk,
    IdempotencyConflict,
    WriteRefused,
    block_authorisation_id,
    derive_key,
)
from finagent.tools.mocks.case_tools import sandbox_connection
from finagent.tools.runner import ToolRunner
from finagent.tools.schemas import BlockPayload, DecisionPayload, RetrievalFilters, payload_hash_of

ACTION_FOR_OUTCOME = {
    Outcome.APPROVE_FOR_POSTING: "POST",
    Outcome.HOLD_FOR_INFORMATION: "HOLD",
    Outcome.ESCALATE_CONTROL_REVIEW: "ESCALATE",
    Outcome.REJECT_DUPLICATE: "REJECT",
    Outcome.REJECT_INVALID: "REJECT",
}
BLOCK_SIGNALS = {
    "BEFORE_REQUEST": "blocked_before_request",
    "AFTER_REJECT": "blocked_after_reject",
    "AFTER_INVALIDATION": "blocked_after_invalidation",
    "AFTER_DUPLICATE": "blocked_after_duplicate_violation",
}
AGENT_ROLE = "agent"
RECEIPT_CHECK = "receipt_evidence"
DUPLICATE_CITATION = "FIN-POL-005#s2"


@dataclass
class NodeContext:
    deps: Any
    tools: ToolRunner
    checkpoint: Callable[[RunState], None]


def _now(ctx: NodeContext) -> Any:
    return ctx.deps.clock.now_utc()


def _read(state: RunState, ctx: NodeContext, tool: str, args: dict[str, Any]) -> Any:
    """Call a read tool. A tool that still fails after its retries becomes a recorded unknown."""
    state.unknowns = [item for item in state.unknowns if item["source"] != tool]
    result = ctx.tools.call(tool, args)
    if result.status == "ok":
        return result.value
    state.unknowns.append(result.unknown.model_dump(mode="json"))
    return None


def _dump(value: Any) -> dict[str, Any] | None:
    return None if value is None else value.model_dump(mode="json")


def _read_vendor(state: RunState, ctx: NodeContext) -> None:
    state.vendor = _dump(_read(state, ctx, "get_vendor_record", {"vendor_id": state.request["vendor_id"]}))


def _read_history(state: RunState, ctx: NodeContext) -> None:
    value = _read(state, ctx, "check_invoice_history", {"vendor_id": state.request["vendor_id"]})
    state.history = [] if value is None else [item.model_dump(mode="json") for item in value.records]


def _read_authority(state: RunState, ctx: NodeContext) -> None:
    value = _read(state, ctx, "get_authority_record", {})
    state.authority = [] if value is None else [item.model_dump(mode="json") for item in value.records]


def _case(state: RunState, ctx: NodeContext) -> Case:
    return Case.model_validate(
        {
            "request": state.request,
            "invoice": state.invoice,
            "vendor": state.vendor,
            "po": state.po,
            "history": state.history,
            "authority": state.authority,
            "evidence": state.evidence,
            "as_of": _now(ctx),
            "unknowns": state.unknowns,
            "retrieved_chunks": state.retrieved,
        }
    )


def _invoice_number(state: RunState) -> str:
    raw = (state.invoice or {}).get("invoice_no") or state.request["invoice_ref"]
    return normalise_invoice_number(raw) or "unknown"


def validate(state: RunState, ctx: NodeContext) -> None:
    state.signal = "valid"


def fetch_invoice(state: RunState, ctx: NodeContext) -> None:
    state.invoice = _dump(
        _read(state, ctx, "get_invoice_document", {"invoice_ref": state.request["invoice_ref"]})
    )
    state.signal = "done"


def fetch_vendor(state: RunState, ctx: NodeContext) -> None:
    _read_vendor(state, ctx)
    state.signal = "done"


def check_history(state: RunState, ctx: NodeContext) -> None:
    _read_history(state, ctx)
    state.signal = "has_po_ref" if (state.invoice or {}).get("po_ref") else "no_po_ref"


def fetch_po(state: RunState, ctx: NodeContext) -> None:
    state.po = _dump(_read(state, ctx, "get_purchase_order", {"po_ref": state.invoice["po_ref"]}))
    state.signal = "done"


def retrieve_policy(state: RunState, ctx: NodeContext) -> None:
    """Fetch rule sources by ID and run the templated queries, as one budgeted tool invocation."""
    ctx.tools.charge()
    started_as_of = _now(ctx)
    retriever = ctx.deps.retriever
    sections: dict[str, dict[str, Any]] = {}
    for item in load_rules().values():
        for citation in item.citations:
            section = retriever.fetch_by_id(citation, AGENT_ROLE, started_as_of)  # denial fails the run
            if section is None:  # missing or not in force: the run must not continue on partial policy
                raise LookupError(f"rule source unavailable: {citation}")
            sections[section.citation] = {
                "citation": section.citation,
                "doc_id": section.doc_id,
                "trust_class": str(section.trust_class),
                "text": section.text,
            }
    state.policy_sections = list(sections.values())
    case = _case(state, ctx)
    filters = RetrievalFilters(case_id=state.case_id, po_ref=(state.po or {}).get("po_ref"))
    receipt_missing = state.po is None or not state.po["receipts"]
    retrieved: dict[str, dict[str, Any]] = {}
    for check, template in TEMPLATES.items():
        if check == RECEIPT_CHECK and not receipt_missing:
            continue
        output = retriever.retrieve_finance_documents(template(case), filters, AGENT_ROLE, started_as_of)
        if check == RECEIPT_CHECK:
            abstained = output.abstained["case_evidence"]
            ctx.deps.audit(state.run_id, "receipt_evidence_query", "abstained" if abstained else "found")
        for lane in ("case_evidence", "non_authoritative"):
            for chunk in output.lanes[lane]:
                retrieved[chunk.citation] = chunk.model_dump(mode="json")
    state.retrieved = list(retrieved.values())
    state.signal = "done"


def reconcile(state: RunState, ctx: NodeContext) -> None:
    _read_authority(state, ctx)
    state.signal = "done"


def _invoice_is_blocked(state: RunState, ctx: NodeContext) -> bool:
    key = f"{state.request['vendor_id']}|{_invoice_number(state)}"
    with sandbox_connection(ctx.deps.sandbox_db) as conn:
        return conn.execute("SELECT 1 FROM blocks WHERE invoice_key = ?", (key,)).fetchone() is not None


def evaluate_case(state: RunState, ctx: NodeContext) -> None:
    if state.refresh:  # after an invalidated approval, decide again on fresh data
        _read_vendor(state, ctx)
        _read_history(state, ctx)
        _read_authority(state, ctx)
        state.refresh = False
    result = evaluate(_case(state, ctx), ctx.deps.clock, invoice_blocked=_invoice_is_blocked(state, ctx))
    state.outcome = str(result.outcome)
    state.primary_category = str(result.primary_category) if result.primary_category else None
    state.owner = result.owner
    state.confidence = str(result.confidence)
    state.next_action = result.next_action
    state.dfa_role = result.dfa_role
    state.sections = {
        "calculations": [item.model_dump(mode="json") for item in result.calculations],
        "assumptions": [item.model_dump(mode="json") for item in result.assumptions],
        "findings": [item.model_dump(mode="json") for item in result.findings],
        "exceptions": [item.model_dump(mode="json") for item in result.exceptions],
        "citations": list(result.citations),
        "inferences": [],
    }
    for event in result.security_events:
        ctx.deps.audit(state.run_id, "security_event", "unlinked_risk_text", doc_id=event["doc_id"])
    state.signal = "done"


def rationale_input(state: RunState) -> RationaleInput:
    sections = state.sections
    lines = (state.invoice or {}).get("lines", [])
    return RationaleInput(
        outcome=state.outcome,
        primary_category=state.primary_category,
        owner=state.owner,
        next_action=state.next_action,
        citations=tuple(sections["citations"]),
        facts=tuple(_facts(state)),
        calculations=tuple(Calculation.model_validate(item) for item in sections["calculations"]),
        exceptions=tuple(ExceptionRecord.model_validate(item) for item in sections["exceptions"]),
        unknowns=tuple(Unknown.model_validate(item) for item in state.unknowns),
        lines=tuple(PromptLine(line["qty"], line["unit_price"], line["line_total"]) for line in lines),
        findings=tuple(PolicyFinding.model_validate(item) for item in sections["findings"]),
    )


def rationale_chunks(state: RunState) -> list[PromptChunk]:
    return [
        PromptChunk(item["citation"], item["doc_id"], item["trust_class"], item["text"])
        for item in (*state.policy_sections, *state.retrieved)
    ]


def _record_rationale(state: RunState, ctx: NodeContext) -> None:
    evaluation = rationale_input(state)
    chunks = rationale_chunks(state)
    digest = prompt_hash(build_prompt(evaluation, chunks))
    if state.llm_response is not None and state.prompt_hash == digest:
        return  # a recorded reply to this exact prompt is not bought again after a crash
    state.prompt_hash, state.model_id, state.llm_response = digest, None, None

    def audit(outcome: str, **detail: Any) -> None:
        ctx.deps.audit(state.run_id, "llm_call", outcome, **detail)

    config = ctx.deps.model_config

    def keep_raw(hop: int, raw_text: str) -> None:
        store_raw(ctx.deps.home / "llm_raw" / f"{state.run_id}-{digest[:12]}-{hop}.txt", raw_text)

    router = Router(ctx.deps.llm, config.rationale_source, config, audit, keep_raw)
    result = router.rationale(evaluation, chunks)
    state.model_id = result.model
    state.llm_response = {
        "source": result.source,
        "rationale": redact(result.rationale.model_dump(mode="json")),
        "calls": result.calls,
        "stop_reasons": result.stop_reasons,
    }


def _template_basis(state: RunState) -> tuple[str, ...]:
    basis = [item["failed_rule"] for item in state.sections["exceptions"]] or ["CALC-GROSS"]
    return tuple(dict.fromkeys(basis))


def _use_template_rationale(state: RunState) -> None:
    state.rationale_source = "TEMPLATE"
    state.sections["inferences"] = [
        Inference(
            id="INF-1",
            statement=f"Recommended {state.outcome}. {state.next_action}.",
            basis_ids=_template_basis(state),
            source="TEMPLATE",
        ).model_dump(mode="json")
    ]


def _use_model_rationale(state: RunState, claims: list[dict[str, Any]]) -> None:
    state.rationale_source = "LLM"
    state.sections["inferences"] = [
        Inference(
            id=f"INF-{index}",
            statement=claim["text"],
            basis_ids=tuple(dict.fromkeys((*claim["citation_ids"], *claim["number_refs"])))
            or _template_basis(state),
            source="LLM",
        ).model_dump(mode="json")
        for index, claim in enumerate(claims, 1)
    ]


def draft_rationale(state: RunState, ctx: NodeContext) -> None:
    """Show the model's explanation only if it passed the validator; otherwise use the template."""
    try:
        _record_rationale(state, ctx)
    except Exception as exc:  # the explanation can fail; the decision must not
        state.llm_response = None
        ctx.deps.audit(state.run_id, "llm_call", "error", error=type(exc).__name__)
    reply = state.llm_response
    if reply is not None and reply["source"] == "LLM":
        _use_model_rationale(state, reply["rationale"]["claims"])
    else:
        _use_template_rationale(state)
    state.signal = "done"


def route(state: RunState, ctx: NodeContext) -> None:
    state.block_context = "BEFORE_REQUEST"
    state.signal = (
        "outcome_approve" if state.outcome == Outcome.APPROVE_FOR_POSTING else "outcome_not_approve"
    )


def _add_action(state: RunState, ctx: NodeContext, tool: str, action: str, **fields: Any) -> None:
    record = Action(id=f"ACT-{len(state.actions) + 1}", tool=tool, action=action, at=_now(ctx), **fields)
    state.actions.append(record.model_dump(mode="json"))


def _apply_duplicate_hold(state: RunState, ctx: NodeContext) -> None:
    """A UNIQUE violation retires the approved POST and asks for a new human decision."""
    policy = SPEC["on_duplicate_unique_violation"]
    ctx.deps.approvals.invalidate(state.approval_id)
    state.outcome = policy["set_outcome"]
    state.primary_category = policy["category"]
    state.owner = policy["owner"]
    state.next_action = "AP Operations compares both records"
    record = ExceptionRecord(
        category=policy["category"],
        failed_rule="UNIQUE_VIOLATION",
        citations=(DUPLICATE_CITATION,),
        owner=policy["owner"],
        next_review_date=next_review_date(ctx.deps.clock.as_of()),
    )
    state.sections["exceptions"] = [record.model_dump(mode="json"), *state.sections["exceptions"]]
    _use_template_rationale(state)  # the model explained the earlier outcome, which no longer stands


def _block_rule(state: RunState) -> str:
    if state.block_rule is not None:
        return state.block_rule
    exceptions = state.sections.get("exceptions") or []
    return exceptions[0]["failed_rule"] if exceptions else "BLOCKED-INVOICE"


def _record_confirmed_block(state: RunState, ctx: NodeContext) -> None:
    """Report a block whose write outcome was unknown but whose row reconciliation found."""
    with sandbox_connection(ctx.deps.sandbox_db) as conn:
        row = conn.execute(
            "SELECT authorisation_id FROM blocks WHERE run_id = ? AND action = 'BLOCK'", (state.run_id,)
        ).fetchone()
    if not any(item["action"] == "BLOCK" for item in state.actions):
        _add_action(
            state,
            ctx,
            "place_payment_block",
            "BLOCK",
            authorisation_id=row[0],
            posting_ref=f"BLOCK-{row[0][:16]}",
        )


def place_block(state: RunState, ctx: NodeContext) -> None:
    if state.block_confirmed:  # reconciliation already proved the row exists
        state.block_confirmed = False
        _record_confirmed_block(state, ctx)
    else:
        rule_id = _block_rule(state)
        authorisation_id = block_authorisation_id(state.run_id, rule_id)
        state.block_rule = rule_id
        state.block_authorisation_id = authorisation_id
        ctx.checkpoint(state)  # decision log VI: the authorisation is durable before the write
        body = BlockPayload(
            case_id=state.case_id,
            vendor_id=state.request["vendor_id"],
            invoice_no_norm=_invoice_number(state),
        )
        args = {
            "run_id": state.run_id,
            "action": "BLOCK",
            "rule_id": rule_id,
            "authorisation": {"type": "SYSTEM_FAILSAFE", "id": authorisation_id},
            "payload": body.model_dump(mode="json"),
            "payload_hash": payload_hash_of(body),
            "idempotency_key": derive_key(state.run_id, authorisation_id, "BLOCK"),
        }
        try:
            result = ctx.tools.call("place_payment_block", args)
        except WriteRefused:
            state.signal = "invariant_breach"
            return
        if result.status == "SUBMIT_UNKNOWN":
            state.pending_write = "BLOCK"
            state.signal = "unknown_result"
            return
        if result.status != "ok":
            state.signal = "invariant_breach"
            return
        if not any(item["action"] == "BLOCK" for item in state.actions):
            _add_action(
                state,
                ctx,
                "place_payment_block",
                "BLOCK",
                authorisation_id=authorisation_id,
                posting_ref=result.value.posting_ref,
            )
    context = state.block_context
    if context == "AFTER_DUPLICATE":
        _apply_duplicate_hold(state, ctx)
    state.block_rule = None
    state.block_authorisation_id = None
    state.pending_write = None
    state.signal = BLOCK_SIGNALS[context]


def _decision_payload(state: RunState, ctx: NodeContext, action: str) -> DecisionPayload:
    invoice = state.invoice or {}
    lines: dict[str, int] = {}
    for line in invoice.get("lines", []):
        if line.get("po_line_ref") and line["qty"] > 0:
            lines[line["po_line_ref"]] = lines.get(line["po_line_ref"], 0) + line["qty"]
    return DecisionPayload(
        case_id=state.case_id,
        vendor_id=state.request["vendor_id"],
        invoice_no_norm=_invoice_number(state),
        amount=invoice.get("gross") or state.request["amount"],
        currency=invoice.get("currency") or state.request["currency"],
        invoice_date=invoice.get("invoice_date") or ctx.deps.clock.as_of(),
        po_ref=invoice.get("po_ref"),
        outcome=state.outcome,
        hold_category=state.primary_category,
        line_quantities=[{"po_line_ref": ref, "qty": qty} for ref, qty in lines.items()],
    )


def request_approval(state: RunState, ctx: NodeContext) -> None:
    action = ACTION_FOR_OUTCOME[Outcome(state.outcome)]
    po = state.po or {}
    receipters = tuple(dict.fromkeys(item["receipter"] for item in po.get("receipts", [])))
    vendor_name = (state.vendor or {}).get("legal_name") or "unknown vendor"
    record = ctx.deps.approvals.create_request(
        ApprovalRequest(
            run_id=state.run_id,
            case_id=state.case_id,
            action=action,
            payload=_decision_payload(state, ctx, action),
            vendor_id=state.request["vendor_id"],
            vendor_name=vendor_name,
            requester=po.get("requester") or "unknown",
            receipters=receipters,
            exceptions=tuple(state.sections.get("exceptions", ())),
            citations=tuple(state.sections.get("citations", ())),
        )
    )
    state.approval_id = record.approval_id
    state.action = record.action
    _add_action(state, ctx, "approval_service", "approval_request", authorisation_id=record.approval_id)
    state.signal = PAUSED


def revalidate(state: RunState, ctx: NodeContext) -> None:
    result = ctx.deps.approvals.revalidate(state.approval_id)
    if result.valid:
        state.signal = "unchanged"
        return
    state.block_context = "AFTER_INVALIDATION"
    state.block_rule = "REVALIDATION"
    state.refresh = True
    state.signal = "changed_or_read_failure"


def _submit_key(state: RunState) -> str:
    return derive_key(state.run_id, state.approval_id, state.action)


def submit(state: RunState, ctx: NodeContext) -> None:
    record = ctx.deps.approvals.get(state.approval_id)
    body = DecisionPayload.model_validate_json(record.payload_json)
    args = {
        "run_id": state.run_id,
        "action": state.action,
        "authorisation": {"type": "HUMAN_APPROVAL", "id": state.approval_id},
        "payload": body.model_dump(mode="json"),
        "payload_hash": record.payload_hash,
        "idempotency_key": _submit_key(state),
    }
    try:
        result = ctx.tools.call("submit_finance_decision", args)
    except DuplicateRisk:
        state.block_context = "AFTER_DUPLICATE"
        state.block_rule = "UNIQUE_VIOLATION"
        state.signal = "duplicate_unique_violation"
        return
    except (Denied, IdempotencyConflict):
        state.signal = "denied"
        return
    if result.status == "SUBMIT_UNKNOWN":
        state.pending_write = "SUBMIT"
        state.signal = "unknown_result"
        return
    if result.status != "ok":
        state.signal = "denied"
        return
    _record_submit(state, ctx, result.value.posting_ref)
    state.signal = "committed"


def _record_submit(state: RunState, ctx: NodeContext, posting_ref: str | None) -> None:
    _add_action(
        state,
        ctx,
        "submit_finance_decision",
        state.action,
        authorisation_id=state.approval_id,
        idempotency_key=_submit_key(state),
        posting_ref=posting_ref,
    )
    state.pending_write = None


def submit_unknown(state: RunState, ctx: NodeContext) -> None:
    """Reconcile from the sandbox first: a committed row outranks an exhausted budget."""
    budget_left = len(state.visited) < ctx.deps.max_steps
    with sandbox_connection(ctx.deps.sandbox_db) as conn:
        if state.pending_write == "BLOCK":
            found = conn.execute(
                "SELECT 1 FROM blocks WHERE run_id = ? AND action = 'BLOCK'", (state.run_id,)
            ).fetchone()
            posting = None
        else:
            found = conn.execute(
                "SELECT response_json FROM idempotency WHERE key = ?", (_submit_key(state),)
            ).fetchone()
            posting = None
            if found is not None:
                posting = conn.execute(
                    "SELECT posting_ref FROM ledger WHERE run_id = ? AND action = ?",
                    (state.run_id, state.action),
                ).fetchone()
    if state.pending_write == "BLOCK":
        if found is not None:
            state.block_confirmed = True
            state.signal = "block_row_found"
        else:
            state.signal = "block_no_row_retry" if budget_left else "no_row_retry_budget_exhausted"
        return
    if found is not None:
        _record_submit(state, ctx, posting[0] if posting else None)
        state.signal = "submit_row_found"
    elif not budget_left:
        state.signal = "no_row_retry_budget_exhausted"
    elif state.action in {"POST", "REJECT"}:
        state.signal = "submit_no_row_retry_post_or_reject"
    else:
        state.signal = "submit_no_row_retry_hold_or_escalate"


HANDLERS: dict[str, Callable[[RunState, NodeContext], None]] = {
    "VALIDATE": validate,
    "FETCH_INVOICE": fetch_invoice,
    "FETCH_VENDOR": fetch_vendor,
    "CHECK_HISTORY": check_history,
    "FETCH_PO": fetch_po,
    "RETRIEVE_POLICY": retrieve_policy,
    "RECONCILE": reconcile,
    "EVALUATE": evaluate_case,
    "DRAFT_RATIONALE": draft_rationale,
    "ROUTE": route,
    "PLACE_BLOCK": place_block,
    "REQUEST_APPROVAL": request_approval,
    "REVALIDATE": revalidate,
    "SUBMIT": submit,
    "SUBMIT_UNKNOWN": submit_unknown,
}


def _facts(state: RunState) -> list[Fact]:
    request = state.request
    source = f"REQUEST:{request['case_id']}"
    facts = [
        Fact(id="FACT-REQUEST-AMOUNT", value=str(request["amount"]), source_id=source),
        Fact(id="FACT-REQUEST-VENDOR", value=request["vendor_id"], source_id=source),
    ]
    if state.invoice:
        invoice_source = f"INVOICE:{state.invoice.get('invoice_no') or request['invoice_ref']}"
        for name in ("invoice_no", "invoice_date", "currency", "gross", "tax", "po_ref"):
            value = state.invoice.get(name)
            if value is not None:
                facts.append(
                    Fact(id=f"FACT-INVOICE-{name.upper()}", value=str(value), source_id=invoice_source)
                )
    if state.vendor:
        vendor_source = f"VENDOR:{state.vendor['vendor_id']}"
        facts.append(Fact(id="FACT-VENDOR-STATUS", value=state.vendor["status"], source_id=vendor_source))
    if state.po:
        po_source = f"PO:{state.po['po_ref']}"
        facts.append(Fact(id="FACT-PO-STATUS", value=state.po["approval_status"], source_id=po_source))
        facts.append(Fact(id="FACT-PO-TOTAL", value=str(state.po["totals"]["incl_tax"]), source_id=po_source))
        for index, receipt in enumerate(state.po["receipts"], 1):
            facts.append(Fact(id=f"FACT-RECEIPT-{index}", value=receipt["qty"], source_id=po_source))
    for record in state.history:
        facts.append(
            Fact(
                id=f"FACT-HISTORY-{record['record_id']}",
                value=record["status"],
                source_id=f"HISTORY:{record['record_id']}",
            )
        )
    return facts


def build_result(state: RunState) -> FinalResult | None:
    """The typed final result, built from the evaluation and the recorded tool outputs."""
    if state.outcome is None:
        return None
    sections = state.sections
    return FinalResult(
        facts=_facts(state),
        calculations=sections.get("calculations", ()),
        inferences=sections.get("inferences", ()),
        assumptions=sections.get("assumptions", ()),
        unknowns=state.unknowns,
        findings=sections.get("findings", ()),
        exceptions=sections.get("exceptions", ()),
        actions_taken=state.actions,
        recommendation=Recommendation(
            outcome=state.outcome,
            primary_category=state.primary_category,
            confidence=Decimal(state.confidence),
            next_action=state.next_action,
            owner=state.owner,
            rationale_source=state.rationale_source,
            citations=(OUTCOME_CITATION,),
        ),
    )
