"""Mock read tools. They read fixture files by path and see the sandbox ledger; no network is used."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from finagent.obs.fingerprint import fingerprint
from finagent.persistence.db import connect
from finagent.tools.schemas import (
    CheckInvoiceHistoryInput,
    CheckInvoiceHistoryOutput,
    GetAuthorityRecordInput,
    GetAuthorityRecordOutput,
    GetInvoiceDocumentInput,
    GetPurchaseOrderInput,
    GetVendorRecordInput,
    HistoryRecord,
    InvoiceDocument,
    PurchaseOrder,
    VendorRecord,
)

# ESCALATE has no history status of its own: it is HELD with its prior outcome (decision log XVII).
LEDGER_STATUS = {"POST": "POSTED", "REJECT": "REJECTED", "HOLD": "HELD", "ESCALATE": "HELD"}


@contextmanager
def sandbox_connection(path: Path) -> Iterator[sqlite3.Connection]:
    """A fresh connection per call, because tools can run on worker threads."""
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


class _CaseTool:
    def __init__(self, case_dir: Path) -> None:
        self._dir = case_dir
        self.case_id = case_dir.name

    def _read(self, name: str) -> Any:
        return json.loads((self._dir / f"{name}.json").read_text(encoding="utf-8"))

    def _require_case(self, case_id: str) -> None:
        if case_id != self.case_id:
            raise ValueError("request is for a different case")


class MockInvoice(_CaseTool):
    def __call__(self, request: GetInvoiceDocumentInput) -> InvoiceDocument:
        self._require_case(request.case_id)
        document = InvoiceDocument.model_validate(self._read("invoice"))
        if document.invoice_no != request.invoice_ref:
            raise ValueError("invoice not found")
        return document


class MockVendor(_CaseTool):
    def __call__(self, request: GetVendorRecordInput) -> VendorRecord:
        self._require_case(request.case_id)
        record = VendorRecord.model_validate(self._read("vendor"))
        if record.vendor_id != request.vendor_id:
            raise ValueError("vendor not found")
        return record


class MockPurchaseOrder(_CaseTool):
    def __init__(self, case_dir: Path, sandbox_path: Path) -> None:
        super().__init__(case_dir)
        self._sandbox = sandbox_path

    def __call__(self, request: GetPurchaseOrderInput) -> PurchaseOrder:
        self._require_case(request.case_id)
        raw = self._read("po")
        if raw["po_ref"] != request.po_ref:
            raise ValueError("purchase order not found")
        with sandbox_connection(self._sandbox) as conn:
            posted = dict(
                conn.execute(
                    "SELECT line.po_line_ref, SUM(line.qty) FROM ledger_lines AS line "
                    "JOIN ledger ON ledger.id = line.ledger_id "
                    "WHERE ledger.action = 'POST' AND line.po_ref = ? GROUP BY line.po_line_ref",
                    (request.po_ref,),
                ).fetchall()
            )
        for line in raw["lines"]:
            line["qty_invoiced_to_date"] += posted.get(line["po_line_ref"], 0)
        return PurchaseOrder.model_validate(raw)


class MockAuthority(_CaseTool):
    def __call__(self, request: GetAuthorityRecordInput) -> GetAuthorityRecordOutput:
        self._require_case(request.case_id)
        return GetAuthorityRecordOutput(records=self._read("authority"))


class MockHistory(_CaseTool):
    """Fixture history plus every sandbox ledger row for the vendor."""

    def __init__(self, case_dir: Path, sandbox_path: Path, hmac_key: str | SecretStr) -> None:
        super().__init__(case_dir)
        self._sandbox = sandbox_path
        self._key = hmac_key

    def _ledger_record(self, row: tuple[Any, ...]) -> HistoryRecord:
        row_id, run_id, action, vendor, number, amount, currency, day, po_ref, outcome, category = row
        identity = f"{vendor}|{number}|{currency}|{amount}"
        return HistoryRecord(
            record_id=f"L-{row_id}",
            status=LEDGER_STATUS[action],
            vendor_id=vendor,
            invoice_no_norm=number,
            currency=currency,
            gross=amount,
            invoice_date=day,
            fingerprint=fingerprint("invoice", identity, self._key),
            po_number=po_ref,
            originating_run=run_id,
            prior_outcome=outcome,
            hold_category=category,
        )

    def __call__(self, request: CheckInvoiceHistoryInput) -> CheckInvoiceHistoryOutput:
        self._require_case(request.case_id)
        # All of the vendor's records are returned: probable-duplicate checks compare different numbers.
        fixture = [item for item in self._read("history") if item["vendor_id"] == request.vendor_id]
        with sandbox_connection(self._sandbox) as conn:
            rows = conn.execute(
                "SELECT id, run_id, action, vendor_id, invoice_no_norm, amount, currency, invoice_date, "
                "po_ref, prior_outcome, hold_category FROM ledger WHERE vendor_id = ? ORDER BY id",
                (request.vendor_id,),
            ).fetchall()
        return CheckInvoiceHistoryOutput(
            records=[
                *[HistoryRecord.model_validate(item) for item in fixture],
                *map(self._ledger_record, rows),
            ]
        )
