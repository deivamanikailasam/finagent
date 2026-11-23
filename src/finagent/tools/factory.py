"""Builds the 8 tools for one case, selected by the impl declared in config/tools.yaml."""

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from finagent.config.clock import Clock
from finagent.persistence.db import connect, migrate
from finagent.rag.retrieve import Retriever
from finagent.tools.block_submit import (
    ApprovalLookup,
    Audit,
    place_payment_block,
    submit_finance_decision,
    write_deadline,
)
from finagent.tools.declarations import ToolsConfig
from finagent.tools.mocks.case_tools import (
    MockAuthority,
    MockHistory,
    MockInvoice,
    MockPurchaseOrder,
    MockVendor,
    sandbox_connection,
)
from finagent.tools.runner import ToolHTTPError
from finagent.tools.schemas import (
    PlacePaymentBlockInput,
    RetrieveFinanceDocumentsInput,
    SubmitFinanceDecisionInput,
)

FAULT_MODES = frozenset({"ok", "timeout_always", "fail_once_503"})
WRITE_DEADLINE_FRACTION = 0.9  # the write gives up before the caller's deadline, so it cannot commit late


class FaultGate:
    """Deterministic fault injection with no sleeps."""

    def __init__(self, mode: str) -> None:
        if mode not in FAULT_MODES:
            raise ValueError(f"unknown fault mode: {mode}")
        self._mode = mode
        self._fired = threading.Event()

    def check(self) -> None:
        if self._mode == "timeout_always":
            raise TimeoutError
        if self._mode == "fail_once_503" and not self._fired.is_set():
            self._fired.set()
            raise ToolHTTPError(503)


def _with_fault(tool: Callable[[Any], Any], gate: FaultGate) -> Callable[[Any], Any]:
    def call(request: Any) -> Any:
        gate.check()
        return tool(request)

    return call


def build_tools(
    case_dir: Path,
    sandbox_path: Path,
    config: ToolsConfig,
    retriever: Retriever,
    approvals: ApprovalLookup,
    clock: Clock,
    hmac_key: str | SecretStr,
    audit: Audit | None = None,
) -> dict[str, Callable[[Any], Any]]:
    conn = connect(sandbox_path)
    try:
        migrate(conn, "sandbox")
    finally:
        conn.close()

    def retrieve(request: RetrieveFinanceDocumentsInput) -> Any:
        return retriever.retrieve_finance_documents(
            request.query, request.filters, request.principal_role, request.as_of
        )

    timeouts = {tool.name: tool.timeout_s * WRITE_DEADLINE_FRACTION for tool in config.tools}

    def block(request: PlacePaymentBlockInput) -> Any:
        with write_deadline(timeouts["place_payment_block"]), sandbox_connection(sandbox_path) as write_conn:
            return place_payment_block(write_conn, request, clock, audit=audit)

    def submit(request: SubmitFinanceDecisionInput) -> Any:
        with (
            write_deadline(timeouts["submit_finance_decision"]),
            sandbox_connection(sandbox_path) as write_conn,
        ):
            return submit_finance_decision(write_conn, request, approvals, clock, audit=audit)

    implementations: dict[tuple[str, str], Callable[[Any], Any]] = {
        ("get_invoice_document", "mock"): MockInvoice(case_dir),
        ("get_vendor_record", "mock"): MockVendor(case_dir),
        ("get_purchase_order", "mock"): MockPurchaseOrder(case_dir, sandbox_path),
        ("get_authority_record", "mock"): MockAuthority(case_dir),
        ("check_invoice_history", "mock"): MockHistory(case_dir, sandbox_path, hmac_key),
        ("retrieve_finance_documents", "real"): retrieve,
        ("place_payment_block", "simulated"): block,
        ("submit_finance_decision", "simulated"): submit,
    }
    faults = json.loads((case_dir / "meta.json").read_text(encoding="utf-8")).get("faults", {})
    unknown = set(faults) - {tool.name for tool in config.tools}
    if unknown:
        raise ValueError(f"fault configured for an unknown tool: {sorted(unknown)}")
    tools = {}
    for declaration in config.tools:
        implementation = implementations.get((declaration.name, declaration.impl))
        if implementation is None:
            raise ValueError(f"no {declaration.impl} implementation for {declaration.name}")
        tools[declaration.name] = _with_fault(implementation, FaultGate(faults.get(declaration.name, "ok")))
    return tools
