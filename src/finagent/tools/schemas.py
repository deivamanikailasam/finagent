"""Input and output models for the 8 tools (decision log VII, XII). Bank values stay masked."""

import hashlib
import json
from datetime import date
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AwareDatetime, BeforeValidator, Field, StrictInt, model_validator

from finagent.domain.enums import (
    AuthorisationType,
    DocType,
    ExceptionCategory,
    HistoryStatus,
    Outcome,
    TrustClass,
    VendorStatus,
)
from finagent.domain.requests import CaseId, RequestAttachment
from finagent.domain.types import Currency, DomainModel, LineMoney, Money, Sha256Hex

Last4 = Annotated[str, Field(pattern=r"^[0-9]{4}$")]
MaskedAccount = Annotated[str, Field(pattern=r"^\*{4}[0-9]{4}$")]
Quantity = Annotated[StrictInt, Field(ge=0)]
Lane = Literal["authoritative", "case_evidence", "non_authoritative"]
RiskFlag = Literal["overseas_account", "manual_payment", "fraud_flag"]


def _blank_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and not value.strip() else value


# A blank header value counts as missing, so it becomes a finding instead of passing as present.
HeaderText = Annotated[str | None, BeforeValidator(_blank_to_none)]


class RemitTo(DomainModel):
    last4: Last4
    fingerprint: Sha256Hex
    country: str = Field(pattern=r"^[A-Z]{2}$")


class InvoiceLine(DomainModel):
    line_id: str = Field(min_length=1)
    po_line_ref: str | None = None
    description_id: str = Field(min_length=1)
    qty: StrictInt
    unit_price: LineMoney
    tax_code: Literal["GST", "FRE"]
    line_total: LineMoney


class InvoiceDocument(DomainModel):
    # Header fields are optional so a missing one becomes a finding, not a crash.
    legal_name: HeaderText = None
    invoice_no: HeaderText = None
    invoice_date: date | None = None
    currency: Currency | None = None
    gross: Money | None = None
    tax: Money | None = None
    po_ref: HeaderText = None
    bill_to_entity: HeaderText = None
    remit_to: RemitTo | None = None
    lines: tuple[InvoiceLine, ...] = ()
    attachments: tuple[RequestAttachment, ...] = Field(default=(), max_length=10)


class VendorModification(DomainModel):
    by: str = Field(min_length=1)
    at: AwareDatetime


class VendorRecord(DomainModel):
    vendor_id: str = Field(min_length=1)
    legal_name: str = Field(min_length=1)
    status: VendorStatus
    payment_details_masked: MaskedAccount
    fingerprint: Sha256Hex
    risk_flags: tuple[RiskFlag, ...] = ()  # closed list: a mistyped flag must not read as clean
    created_by: str = Field(min_length=1)
    created_at: AwareDatetime
    modification_history: tuple[VendorModification, ...] = ()
    bank_changed_at: AwareDatetime | None = None
    last_paid_at: AwareDatetime | None = None
    last_updated: AwareDatetime


class PoTotals(DomainModel):
    excl_tax: Money
    incl_tax: Money


class Tolerances(DomainModel):
    goods_absolute: Money
    goods_percent: Money
    services_absolute: Money
    services_percent: Money
    freight_absolute: Money


class PoLine(DomainModel):
    po_line_ref: str = Field(min_length=1)
    kind: Literal["goods", "services", "freight"]
    qty: Quantity
    unit_price: Money
    line_value: Money
    qty_invoiced_to_date: Quantity = 0


class Receipt(DomainModel):
    po_line_ref: str = Field(min_length=1)
    receipter: str = Field(min_length=1)
    qty: Quantity
    date: date
    service_confirmed_by: str | None = None


class PurchaseOrder(DomainModel):
    po_ref: str = Field(min_length=1)
    approval_status: str = Field(min_length=1)
    requester: str = Field(min_length=1)
    currency: Currency
    totals: PoTotals
    tolerances: Tolerances
    freight_permitted: bool = False
    lines: tuple[PoLine, ...] = ()
    receipts: tuple[Receipt, ...] = ()


class HistoryRecord(DomainModel):
    record_id: str = Field(min_length=1)
    status: HistoryStatus
    vendor_id: str = Field(min_length=1)
    invoice_no_norm: str = Field(min_length=1)
    currency: Currency
    gross: Money
    invoice_date: date
    fingerprint: Sha256Hex | None = None
    po_number: str | None = None
    attachment_hash: Sha256Hex | None = None
    originating_run: str | None = None
    prior_outcome: Outcome | None = None
    hold_category: ExceptionCategory | None = None


class Delegation(DomainModel):
    delegate: str = Field(min_length=1)
    delegator: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    start: date
    end: date

    @model_validator(mode="after")
    def _end_not_before_start(self) -> "Delegation":
        if self.end < self.start:
            raise ValueError("delegation end is before its start")
        return self


class AuthorityRecord(DomainModel):
    approver_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    limit: Money
    delegations: tuple[Delegation, ...] = ()
    register_version: str = Field(min_length=1)
    last_updated: AwareDatetime


class RetrievalItem(DomainModel):
    float_fields: ClassVar[frozenset[str]] = frozenset({"score"})  # a BM25 relevance score, never money

    doc_id: str = Field(min_length=1)
    doc_type: DocType
    version: str = Field(min_length=1)
    section: str = Field(min_length=1)
    score: float = Field(gt=0, allow_inf_nan=False)
    lane: Lane
    trust_class: TrustClass
    citation: str = Field(min_length=1)
    text: str


class RetrievalFilters(DomainModel):
    doc_type: DocType | None = None
    case_id: CaseId | None = None
    po_ref: str | None = None


class Authorisation(DomainModel):
    type: AuthorisationType
    id: str = Field(min_length=1)


class BlockPayload(DomainModel):
    case_id: CaseId
    vendor_id: str = Field(min_length=1)
    invoice_no_norm: str = Field(min_length=1)


class LineQuantity(DomainModel):
    po_line_ref: str = Field(min_length=1)
    qty: Quantity
    received: Quantity | None = None  # bound to the approval, and rechecked inside the POST transaction


class DecisionPayload(BlockPayload):
    amount: Money
    currency: Currency
    # Stored on the ledger, so later runs can rebuild sticky outcomes and cumulative quantities.
    invoice_date: date
    po_ref: str | None = None
    outcome: Outcome
    hold_category: ExceptionCategory | None = None
    line_quantities: tuple[LineQuantity, ...] = ()
    commitment: Money | None = None  # max(invoice gross, PO total): what an approver's limit must cover


def payload_hash_of(payload: DomainModel) -> str:
    """SHA-256 of the payload's canonical JSON. Money is already in one canonical form."""
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class WriteInput(DomainModel):
    run_id: str = Field(min_length=1)
    action: str = Field(min_length=1)  # a plain string so the tool itself denies an unknown action
    authorisation: Authorisation
    payload: BlockPayload
    payload_hash: Sha256Hex
    idempotency_key: Sha256Hex

    @model_validator(mode="after")
    def _hash_matches_payload(self) -> "WriteInput":
        # An approval is bound to this hash, so it must describe the payload actually submitted.
        if self.payload_hash != payload_hash_of(self.payload):
            raise ValueError("payload_hash does not match the payload")
        return self


class WriteOutput(DomainModel):
    status: str = Field(min_length=1)
    posting_ref: str | None = None
    amount: Money | None = None


class GetInvoiceDocumentInput(DomainModel):
    case_id: CaseId
    invoice_ref: str = Field(min_length=1)


class GetVendorRecordInput(DomainModel):
    case_id: CaseId
    vendor_id: str = Field(min_length=1)


class GetPurchaseOrderInput(DomainModel):
    case_id: CaseId
    po_ref: str = Field(min_length=1)


class GetAuthorityRecordInput(DomainModel):
    case_id: CaseId


class GetAuthorityRecordOutput(DomainModel):
    records: tuple[AuthorityRecord, ...] = ()


class CheckInvoiceHistoryInput(DomainModel):
    case_id: CaseId
    vendor_id: str = Field(min_length=1)
    invoice_no_norm: str | None = None


class CheckInvoiceHistoryOutput(DomainModel):
    records: tuple[HistoryRecord, ...] = ()


class RetrieveFinanceDocumentsInput(DomainModel):
    query: str = Field(min_length=1)
    filters: RetrievalFilters = RetrievalFilters()
    principal_role: str = Field(min_length=1)
    as_of: AwareDatetime


class RetrieveFinanceDocumentsOutput(DomainModel):
    float_fields: ClassVar[frozenset[str]] = frozenset({"lanes"})  # each item still checks its own fields

    lanes: dict[Lane, tuple[RetrievalItem, ...]]
    abstained: dict[Lane, bool]
    abstain_reason: dict[Lane, Literal["no_candidate", "non_positive", "low_coverage"]] = Field(
        default_factory=dict
    )


class PlacePaymentBlockInput(WriteInput):
    rule_id: str = Field(min_length=1)  # the block is bound to (run_id, rule_id, action)


class SubmitFinanceDecisionInput(WriteInput):
    payload: DecisionPayload


TOOL_SCHEMAS: dict[str, tuple[type[DomainModel], type[DomainModel]]] = {
    "get_invoice_document": (GetInvoiceDocumentInput, InvoiceDocument),
    "get_vendor_record": (GetVendorRecordInput, VendorRecord),
    "get_purchase_order": (GetPurchaseOrderInput, PurchaseOrder),
    "get_authority_record": (GetAuthorityRecordInput, GetAuthorityRecordOutput),
    "check_invoice_history": (CheckInvoiceHistoryInput, CheckInvoiceHistoryOutput),
    "retrieve_finance_documents": (RetrieveFinanceDocumentsInput, RetrieveFinanceDocumentsOutput),
    "place_payment_block": (PlacePaymentBlockInput, WriteOutput),
    "submit_finance_decision": (SubmitFinanceDecisionInput, WriteOutput),
}
