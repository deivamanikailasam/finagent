"""Input and output models for the 8 tools (decision log VII, XII). Bank values stay masked."""

from datetime import date
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AwareDatetime, Field, StrictInt

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
    legal_name: str | None = None
    invoice_no: str | None = None
    invoice_date: date | None = None
    currency: Currency | None = None
    gross: Money | None = None
    tax: Money | None = None
    po_ref: str | None = None
    bill_to_entity: str | None = None
    remit_to: RemitTo | None = None
    lines: tuple[InvoiceLine, ...] = ()
    attachments: tuple[RequestAttachment, ...] = ()


class VendorModification(DomainModel):
    by: str = Field(min_length=1)
    at: AwareDatetime


class VendorRecord(DomainModel):
    vendor_id: str = Field(min_length=1)
    legal_name: str = Field(min_length=1)
    status: VendorStatus
    payment_details_masked: MaskedAccount
    fingerprint: Sha256Hex
    risk_flags: tuple[str, ...] = ()
    created_by: str = Field(min_length=1)
    created_at: AwareDatetime
    modification_history: tuple[VendorModification, ...] = ()
    bank_changed_at: AwareDatetime | None = None
    last_paid_at: AwareDatetime | None = None
    last_updated: AwareDatetime


class PoTotals(DomainModel):
    excl_tax: Money
    incl_tax: Money


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
    tolerances: dict[str, Money] = Field(default_factory=dict)
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
    score: float = Field(gt=0)
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


class WriteInput(DomainModel):
    run_id: str = Field(min_length=1)
    action: str = Field(min_length=1)  # a plain string so the tool itself denies an unknown action
    authorisation: Authorisation
    payload: dict[str, Any]
    payload_hash: Sha256Hex
    idempotency_key: Sha256Hex


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
    pass


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
