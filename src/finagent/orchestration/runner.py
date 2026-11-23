"""Runs the graph to its next pause or terminal state, with a checkpoint after every node."""

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from finagent.approvals.service import ApprovalService
from finagent.config.clock import Clock
from finagent.config.settings import load_settings
from finagent.domain.enums import RunStatus
from finagent.domain.requests import ProcessingRequest
from finagent.domain.results import FinalResult
from finagent.llm.anthropic_adapter import ModelConfig, load_model_config
from finagent.llm.client import LLMClient
from finagent.obs.log import run_context
from finagent.orchestration.checkpoint import fingerprints, rehydrate, restore
from finagent.orchestration.graph import PAUSE_NODES, PAUSED, TERMINAL, RunState, next_node
from finagent.orchestration.nodes import HANDLERS, NodeContext, build_result
from finagent.persistence.db import connect, migrate, transaction
from finagent.persistence.locks import run_lock
from finagent.persistence.repos import ApprovalRepo, AuditRepo, CheckpointRepo, RunRepo
from finagent.rag.loader import load_verified
from finagent.rag.retrieve import Retriever, load_case_documents
from finagent.tools.block_submit import ApprovalView, derive_key
from finagent.tools.declarations import ToolsConfig, load_tool_declarations
from finagent.tools.factory import build_tools
from finagent.tools.runner import (
    BudgetExceeded,
    Executor,
    RunContext,
    ThreadExecutor,
    ToolRunner,
    fault_kind,
)
from finagent.tools.schemas import (
    CheckInvoiceHistoryInput,
    GetAuthorityRecordInput,
    GetInvoiceDocumentInput,
    GetPurchaseOrderInput,
    GetVendorRecordInput,
)

ROOT = Path(__file__).resolve().parents[3]
TERMINAL_STATUS = {
    "COMPLETE": RunStatus.COMPLETE,
    "REJECTED_BY_APPROVER": RunStatus.REJECTED_BY_APPROVER,
    "FAILED": RunStatus.FAILED,
}


@dataclass(frozen=True)
class RunView:
    run_id: str
    status: str
    current_node: str
    outcome: str | None
    primary_category: str | None
    visited: tuple[str, ...]
    result: FinalResult | None
    pending_approval_id: str | None


class _ApprovalLookup:
    """The submit tool's read-only view of approvals."""

    def __init__(self, app_db: Path) -> None:
        self._app_db = app_db

    def get(self, approval_id: str) -> ApprovalView | None:
        conn = connect(self._app_db)  # tools run on worker threads, and a connection stays on its own
        try:
            record = ApprovalRepo(conn).get(approval_id)
        finally:
            conn.close()
        if record is None:
            return None
        return ApprovalView(
            record.approval_id, record.run_id, record.payload_hash, record.action, record.status
        )


@dataclass
class RunDeps:
    case_dir: Path
    home: Path
    clock: Clock
    llm: LLMClient
    executor: Executor
    config: ToolsConfig
    retriever: Retriever
    tools: dict[str, Callable[[Any], Any]]
    approvals: ApprovalService
    conn: Any
    app_db: Path
    sandbox_db: Path
    max_steps: int
    crash_hook: Callable[[str], None] | None
    model_config: ModelConfig
    request: dict[str, Any]

    def write_audit(self, event: dict[str, Any]) -> None:
        """A fresh connection per event, because tools can report from worker threads."""
        conn = connect(self.app_db)
        try:
            AuditRepo(conn).append(event)
        finally:
            conn.close()

    def audit(self, run_id: str, event: str, outcome: str, **detail: Any) -> None:
        self.write_audit(
            {
                "ts": self.clock.now_utc().isoformat(),
                "run_id": run_id,
                "event": event,
                "outcome": outcome,
                "duration_ms": 0.0,
                **detail,
            }
        )


def build_deps(
    case_dir: Path,
    home: Path,
    clock: Clock,
    llm: LLMClient,
    max_steps: int | None = None,
    executor: Executor | None = None,
    crash_hook: Callable[[str], None] | None = None,
    request: dict[str, Any] | None = None,
) -> RunDeps:
    case_dir, home = Path(case_dir), Path(home)
    home.mkdir(parents=True, exist_ok=True)
    app_db, sandbox_db = home / "finagent.db", home / "erp_sandbox.db"
    conn = connect(app_db)
    migrate(conn, "app")
    config = load_tool_declarations(ROOT / "config/tools.yaml")
    result = load_verified(ROOT / "corpus", ROOT / "config/registry.yaml", home / "index_manifest.json")
    retriever = Retriever(result, load_case_documents(case_dir.parent))
    lookup = _ApprovalLookup(app_db)
    audit_sink: list[Callable[[dict[str, Any]], None]] = []

    def forward(event: dict[str, Any]) -> None:
        audit_sink[0](event)

    tools = build_tools(
        case_dir, sandbox_db, config, retriever, lookup, clock, load_settings().hmac_key, audit=forward
    )
    case_id = case_dir.name
    run_request = request or json.loads((case_dir / "request.json").read_text(encoding="utf-8"))
    vendor_id = run_request["vendor_id"]
    run_executor = executor or ThreadExecutor()
    declared = {tool.name: tool for tool in config.tools}

    def bounded(name: str, request: Any) -> Any:
        """Approval-path reads keep the tool's deadline and retries, so one blip cannot block an invoice."""
        tool, backoff = declared[name], config.backoff
        for attempt in range(1, tool.max_attempts + 1):
            try:
                return run_executor.run(lambda: tools[name](request), tool.timeout_s, lambda _late: None)
            except Exception as exc:
                if fault_kind(exc) not in tool.retry_on or attempt == tool.max_attempts:
                    raise
                run_executor.sleep(min(backoff.base_s * backoff.multiplier ** (attempt - 1), backoff.max_s))
        raise RuntimeError("unreachable: a tool has at least one attempt")  # pragma: no cover

    def optional(name: str, request: Any) -> Any:
        try:
            return bounded(name, request)
        except Exception:  # recorded as None, so a value that later changes or vanishes invalidates
            return None

    def documents() -> dict[str, Any]:
        invoice_ref = run_request["invoice_ref"]
        invoice = optional(
            "get_invoice_document", GetInvoiceDocumentInput(case_id=case_id, invoice_ref=invoice_ref)
        )
        if invoice is None:
            return {"invoice": None}
        po = None
        if invoice.po_ref:
            po = optional("get_purchase_order", GetPurchaseOrderInput(case_id=case_id, po_ref=invoice.po_ref))
        return {
            "gross": str(invoice.gross),
            "remit": invoice.remit_to.fingerprint if invoice.remit_to else None,
            "po_status": str(po.approval_status) if po else None,
            "po_total": str(po.totals.incl_tax) if po else None,
        }

    approvals = ApprovalService(
        conn,
        clock,
        lambda: bounded("get_authority_record", GetAuthorityRecordInput(case_id=case_id)).records,
        lambda: bounded("get_vendor_record", GetVendorRecordInput(case_id=case_id, vendor_id=vendor_id)),
        lambda: (
            bounded(
                "check_invoice_history", CheckInvoiceHistoryInput(case_id=case_id, vendor_id=vendor_id)
            ).records
        ),
        documents,
    )
    deps = RunDeps(
        case_dir=case_dir,
        home=home,
        clock=clock,
        llm=llm,
        executor=run_executor,
        config=config,
        retriever=retriever,
        tools=tools,
        approvals=approvals,
        conn=conn,
        app_db=app_db,
        sandbox_db=sandbox_db,
        max_steps=config.budget.max_steps if max_steps is None else max_steps,
        crash_hook=crash_hook,
        model_config=load_model_config(ROOT / "config/model.yaml"),
        request=run_request,
    )
    audit_sink.append(deps.write_audit)
    return deps


class Runner:
    def __init__(self, deps: RunDeps) -> None:
        self.deps = deps
        self._runs = RunRepo(deps.conn)
        self._checkpoints = CheckpointRepo(deps.conn)

    def _tools(self, state: RunState) -> ToolRunner:
        context = RunContext(state.run_id, state.case_id, self.deps.write_audit, self.deps.clock)
        runner = ToolRunner(self.deps.tools, self.deps.config, self.deps.executor, context)
        runner.tool_calls = state.tool_calls
        return runner

    def start(self) -> RunView:
        deps = self.deps

        def read(name: str) -> Any:
            return json.loads((deps.case_dir / f"{name}.json").read_text(encoding="utf-8"))

        request = ProcessingRequest.model_validate(deps.request)  # invalid input never becomes a run
        run_id = str(uuid.uuid4())
        now = deps.clock.now_utc()
        self._runs.create(run_id, request.case_id, RunStatus.RUNNING, "VALIDATE", now, now)
        corpus_hash, config_hash = fingerprints()
        state = RunState(
            run_id=run_id,
            case_id=request.case_id,
            case_dir=str(deps.case_dir.resolve()),
            request=request.model_dump(mode="json"),
            evidence=read("evidence"),
            corpus_hash=corpus_hash,
            config_hash=config_hash,
        )
        self._save(state)
        return self._drive(state)

    def resume(self, run_id: str) -> RunView:
        """Continue after a decision. A run that is still waiting, or finished, is returned unchanged."""
        with run_lock(self.deps.home, run_id):  # two commands can never drive one run at once
            state = restore(self._checkpoints, run_id)
            if state.node in TERMINAL:
                return self._view(state)
            if state.node in PAUSE_NODES and state.signal == PAUSED:
                if not self._rehydrate_or_fail(state):
                    return self._view(state)
                if not self._leave_pause(state):
                    return self._view(state)
                return self._drive(state)
            if not self._rehydrate_or_fail(state):
                return self._view(state)
            self._revalidate_before_retry(state)
            return self._drive(state)

    def view(self, run_id: str) -> RunView:
        """The run as it stands, without moving it."""
        return self._view(restore(self._checkpoints, run_id))

    def _rehydrate_or_fail(self, state: RunState) -> bool:
        try:
            rehydrate(state, self.deps.retriever, self.deps.clock.now_utc())
            return True
        except Exception as exc:  # the controls must not re-run on missing evidence
            self.deps.audit(state.run_id, "node_error", type(exc).__name__, node=state.node)
            state.signal = "invariant_breach"
            self._advance(state.node, state)
            return False

    def _revalidate_before_retry(self, state: RunState) -> None:
        """After a crash at SUBMIT, a POST or REJECT with no committed row is checked again first."""
        if state.node != "SUBMIT" or state.action not in {"POST", "REJECT"}:
            return
        key = derive_key(state.run_id, state.approval_id, state.action)
        conn = connect(self.deps.sandbox_db)
        try:
            committed = conn.execute("SELECT 1 FROM idempotency WHERE key = ?", (key,)).fetchone()
        finally:
            conn.close()
        if committed is None:
            state.node = "REVALIDATE"

    def _leave_pause(self, state: RunState) -> bool:
        record = self.deps.approvals.get(state.approval_id)
        if record is None:
            state.signal = "invariant_breach"
        elif record.run_id != state.run_id or record.status == "PENDING":
            # A run attached to another run's request never submits; that run owns the decision.
            return False
        elif record.status == "REJECTED":
            state.block_context = "AFTER_REJECT"
            state.block_rule = "APPROVER_REJECT"
            state.signal = "approver_rejected"
        elif record.action in {"HOLD", "ESCALATE"} and record.status == "APPROVED":
            state.signal = "approved_hold_or_escalate"
        else:
            state.signal = "approved_post_or_reject"  # REVALIDATE also handles an invalidated request
        self._advance(state.node, state)
        return True

    def _advance(self, node: str, state: RunState) -> None:
        state.node = next_node(node, state)
        if state.node in TERMINAL:
            state.visited.append(state.node)  # in the same checkpoint, so a crash cannot lose it
        self._save(state)

    def _save(self, state: RunState) -> None:
        now = self.deps.clock.now_utc()
        status = self._status(state)
        result = build_result(state) if status != RunStatus.RUNNING else None
        result_json = result.model_dump_json() if result is not None else None
        with transaction(self.deps.conn):  # the checkpoint and the run row never disagree
            self._checkpoints.append(state.run_id, state.node, state.to_json(), now)
            self._runs.update(state.run_id, status, state.node, now, result_json)
            self._runs.record_hashes(
                state.run_id, state.corpus_hash, state.prompt_hash, state.config_hash, state.model_id
            )

    @staticmethod
    def _status(state: RunState) -> RunStatus:
        if state.node in TERMINAL:
            return TERMINAL_STATUS[state.node]
        if state.node in PAUSE_NODES and state.signal == PAUSED:
            return RunStatus.AWAITING_APPROVAL
        return RunStatus.RUNNING

    def _pending_approval(self, state: RunState, status: RunStatus) -> str | None:
        """Only a request that is still pending can be offered for a decision."""
        if status != RunStatus.AWAITING_APPROVAL or state.approval_id is None:
            return None
        record = self.deps.approvals.get(state.approval_id)
        return state.approval_id if record is not None and record.status == "PENDING" else None

    def _view(self, state: RunState) -> RunView:
        status = self._status(state)
        return RunView(
            run_id=state.run_id,
            status=str(status),
            current_node=state.node,
            outcome=state.outcome,
            primary_category=state.primary_category,
            visited=tuple(state.visited),
            result=build_result(state),
            pending_approval_id=self._pending_approval(state, status),
        )

    def _step(self, node: str, state: RunState, context: NodeContext) -> None:
        started = time.perf_counter()
        state.visited.append(node)
        try:
            with run_context(state.run_id):
                HANDLERS[node](state, context)
        except BudgetExceeded:
            state.signal = "budget_exhausted"
        except Exception as exc:  # an unexpected error fails safe: nothing further is written
            self.deps.audit(state.run_id, "node_error", type(exc).__name__, node=node)
            state.signal = "invariant_breach"
        state.tool_calls = context.tools.tool_calls
        self.deps.write_audit(
            {
                "ts": self.deps.clock.now_utc().isoformat(),
                "run_id": state.run_id,
                "event": "node_completed",
                "outcome": str(state.signal),
                "duration_ms": (time.perf_counter() - started) * 1000,
                "node": node,
            }
        )

    def _drive(self, state: RunState) -> RunView:
        context = NodeContext(self.deps, self._tools(state), self._save)
        while state.node not in TERMINAL:
            node = state.node
            # Reconciliation still runs on a spent budget: a committed row must be found first.
            if len(state.visited) >= self.deps.max_steps and node != "SUBMIT_UNKNOWN":
                state.signal = "budget_exhausted"
            else:
                self._step(node, state, context)
                if state.signal == PAUSED:
                    self._save(state)
                    return self._view(state)
                if node == "SUBMIT" and state.signal == "committed" and self.deps.crash_hook:
                    self.deps.crash_hook("after_submit_before_checkpoint")
            self._advance(node, state)
        return self._view(state)
