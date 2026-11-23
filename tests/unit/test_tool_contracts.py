"""WP 4.3: each tool exposes the brief's minimum fields, as listed in the build guide."""

from pathlib import Path

import pytest

from finagent.tools.declarations import load_tool_declarations
from finagent.tools.schemas import (
    TOOL_SCHEMAS,
    HistoryRecord,
    PurchaseOrder,
    RetrievalItem,
    SubmitFinanceDecisionInput,
    VendorRecord,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_tool_declarations(ROOT / "config/tools.yaml")


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (RetrievalItem, {"doc_id", "doc_type", "version", "section", "score", "citation"}),
        (VendorRecord, {"status", "payment_details_masked", "risk_flags", "last_updated"}),
        (PurchaseOrder, {"lines", "totals", "currency", "tolerances", "approval_status", "receipts"}),
        (HistoryRecord, {"record_id", "fingerprint", "status"}),
        (SubmitFinanceDecisionInput, {"authorisation", "payload", "payload_hash", "idempotency_key"}),
    ],
)
def test_minimum_fields(model, fields):
    assert fields <= set(model.model_fields)


def test_every_tool_has_a_schema_purpose_and_access():
    assert {tool.name for tool in CONFIG.tools} == set(TOOL_SCHEMAS)
    for tool in CONFIG.tools:
        assert tool.purpose.strip()
        assert tool.access in {"read", "write"}


def test_only_block_and_submit_are_writes():
    writes = {tool.name for tool in CONFIG.tools if tool.access == "write"}
    assert writes == {"place_payment_block", "submit_finance_decision"}


def test_write_tools_are_simulated_and_retrieval_is_real():
    impl = {tool.name: tool.impl for tool in CONFIG.tools}
    assert impl["place_payment_block"] == impl["submit_finance_decision"] == "simulated"
    assert impl["retrieve_finance_documents"] == "real"
    assert {impl[name] for name in impl if name.startswith(("get_", "check_"))} == {"mock"}
