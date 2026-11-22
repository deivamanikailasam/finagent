import re
import typing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from finagent.domain.case import Case
from finagent.tools.declarations import load_tool_declarations
from finagent.tools.schemas import (
    TOOL_SCHEMAS,
    AuthorityRecord,
    HistoryRecord,
    InvoiceDocument,
    InvoiceLine,
    PlacePaymentBlockInput,
    PoLine,
    PoTotals,
    PurchaseOrder,
    Receipt,
    RetrievalItem,
    RetrieveFinanceDocumentsOutput,
    VendorRecord,
    WriteOutput,
)

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 64
NOW = datetime(2026, 9, 15, tzinfo=UTC)


def line(**overrides):
    fields = {
        "line_id": "L1",
        "po_line_ref": "PO-L1",
        "description_id": "D1",
        "qty": 40,
        "unit_price": "150.00",
        "tax_code": "GST",
        "line_total": "6000.00",
    }
    return {**fields, **overrides}


def po_line(**overrides):
    fields = {
        "po_line_ref": "PO-L1",
        "kind": "goods",
        "qty": 40,
        "unit_price": "150.00",
        "line_value": "6000.00",
    }
    return {**fields, **overrides}


def history(**overrides):
    fields = {
        "record_id": "H-0001",
        "status": "PAID",
        "vendor_id": "V-100",
        "invoice_no_norm": "inv1001",
        "currency": "AUD",
        "gross": "9900.00",
        "invoice_date": date(2026, 9, 1),
    }
    return {**fields, **overrides}


def authority(**overrides):
    fields = {
        "approver_id": "cc-manager-01",
        "role": "Cost Centre Manager",
        "limit": "10000.00",
        "register_version": "v2026.08",
        "last_updated": NOW,
    }
    return {**fields, **overrides}


def vendor(**overrides):
    fields = {
        "vendor_id": "V-100",
        "legal_name": "Example Supplies Pty Ltd",
        "status": "ACTIVE",
        "payment_details_masked": "****1111",
        "fingerprint": SHA,
        "created_by": "vg-officer-01",
        "created_at": NOW,
        "last_updated": NOW,
    }
    return {**fields, **overrides}


def test_schemas_cover_exactly_the_declared_tools():
    config = load_tool_declarations(ROOT / "config/tools.yaml")
    assert set(TOOL_SCHEMAS) == {tool.name for tool in config.tools}


def test_invoice_header_fields_are_all_optional():
    document = InvoiceDocument()
    assert document.invoice_no is None
    assert document.gross is None
    for name in (
        "legal_name",
        "invoice_no",
        "invoice_date",
        "currency",
        "gross",
        "tax",
        "po_ref",
        "bill_to_entity",
    ):
        assert not InvoiceDocument.model_fields[name].is_required()


@pytest.mark.parametrize(
    ("model", "build", "field"),
    [
        (InvoiceLine, line, "unit_price"),
        (InvoiceLine, line, "line_total"),
        (PoLine, po_line, "unit_price"),
        (PoLine, po_line, "line_value"),
        (HistoryRecord, history, "gross"),
        (AuthorityRecord, authority, "limit"),
    ],
)
@pytest.mark.parametrize("bad", [150.0, "150.001", "NaN"])
def test_money_rejects_floats_and_more_than_two_places(model, build, field, bad):
    assert model(**build())
    with pytest.raises(ValidationError):
        model(**build(**{field: bad}))


@pytest.mark.parametrize("field", ["gross", "tax"])
@pytest.mark.parametrize("bad", [9900.0, "9900.001", "-1.00"])
def test_invoice_header_money(field, bad):
    with pytest.raises(ValidationError):
        InvoiceDocument(**{field: bad})


@pytest.mark.parametrize("model", [PoTotals])
def test_po_totals_reject_floats(model):
    with pytest.raises(ValidationError):
        model(excl_tax=9000.0, incl_tax="9900.00")


def test_negative_money_is_allowed_only_on_invoice_lines():
    assert InvoiceLine(**line(unit_price="-30.00", line_total="-1200.00")).unit_price == Decimal("-30.00")
    with pytest.raises(ValidationError):
        PoLine(**po_line(unit_price="-30.00"))
    with pytest.raises(ValidationError):
        HistoryRecord(**history(gross="-9900.00"))
    with pytest.raises(ValidationError):
        AuthorityRecord(**authority(limit="-1.00"))
    with pytest.raises(ValidationError):
        WriteOutput(status="OK", amount="-1.00")


@pytest.mark.parametrize("masked", ["12345678", "****111", "1234****", "****abcd"])
def test_vendor_holds_masked_bank_values_only(masked):
    with pytest.raises(ValidationError):
        VendorRecord(**vendor(payment_details_masked=masked))


def test_remit_to_holds_last4_and_fingerprint_only():
    with pytest.raises(ValidationError):
        InvoiceDocument(remit_to={"last4": "12345678", "fingerprint": SHA, "country": "AU"})
    with pytest.raises(ValidationError):
        InvoiceDocument(remit_to={"last4": "1111", "fingerprint": "raw", "country": "AU"})
    with pytest.raises(ValidationError):
        InvoiceDocument(
            remit_to={"last4": "1111", "fingerprint": SHA, "country": "AU", "account": "12345678"}
        )


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValidationError):
        VendorRecord(**vendor(created_at=datetime(2026, 9, 15, 9, 0)))


def test_history_has_prior_outcome_and_hold_category():
    record = HistoryRecord(**history(prior_outcome="HOLD_FOR_INFORMATION", hold_category="BANK_CHANGE"))
    assert (record.prior_outcome, record.hold_category) == ("HOLD_FOR_INFORMATION", "BANK_CHANGE")


def test_write_input_requires_64_hex_key_and_rejects_float_payload():
    fields = {
        "run_id": "run-1",
        "action": "BLOCK",
        "rule_id": "TOL-GOODS",
        "authorisation": {"type": "SYSTEM_FAILSAFE", "id": "auth-1"},
        "payload": {"amount": "9900.00"},
        "payload_hash": SHA,
        "idempotency_key": SHA,
    }
    assert PlacePaymentBlockInput(**fields).rule_id == "TOL-GOODS"
    with pytest.raises(ValidationError):
        PlacePaymentBlockInput(**{**fields, "idempotency_key": "short"})
    with pytest.raises(ValidationError):
        PlacePaymentBlockInput(**{**fields, "payload": {"nested": [{"amount": 9900.0}]}})


def retrieval_item(**overrides):
    fields = {
        "doc_id": "FIN-POL-003",
        "doc_type": "POLICY",
        "version": "4.0",
        "section": "s2",
        "score": 1.5,
        "lane": "authoritative",
        "trust_class": "AUTHORITATIVE",
        "citation": "FIN-POL-003#s2",
        "text": "table",
    }
    return {**fields, **overrides}


def test_only_the_relevance_score_may_be_a_float():
    output = RetrieveFinanceDocumentsOutput(
        lanes={"authoritative": [retrieval_item()]},
        abstained={"authoritative": False},
    )
    assert output.lanes["authoritative"][0].score == 1.5
    with pytest.raises(ValidationError):
        RetrievalItem(**retrieval_item(version=4.0))
    with pytest.raises(ValidationError):
        RetrievalItem(**retrieval_item(score=0))


def _model_in(annotation):
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for argument in typing.get_args(annotation):
        found = _model_in(argument)
        if found:
            return found
    return None


def _spec_overrides():
    spec = yaml.safe_load((ROOT / "spec/policy_rules.yaml").read_text(encoding="utf-8"))
    for rule in spec["rules"]:
        for vector in rule["vectors"]:
            yield from ((vector["name"], path, value) for path, value in vector["overrides"].items())


def test_every_vector_override_path_exists_in_the_schema():
    for name, path, value in _spec_overrides():
        model = Case
        field = None
        for part in re.findall(r"[^.\[\]]+", path):
            if part.isdigit():
                continue
            assert model is not None and part in model.model_fields, f"{name}: {path} at {part}"
            field = model.model_fields[part]
            model = _model_in(field.annotation)
        if isinstance(value, dict):
            assert model is not None, f"{name}: {path}"
            model(**value)


@pytest.mark.parametrize("model", [Receipt, PurchaseOrder, VendorRecord, InvoiceDocument])
def test_models_are_closed(model):
    assert model.model_config["extra"] == "forbid"
    assert model.model_config["frozen"] is True
