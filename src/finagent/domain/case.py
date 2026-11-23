"""The typed input every controls module takes."""

from typing import ClassVar, Literal

from pydantic import AwareDatetime, Field

from finagent.domain.requests import ProcessingRequest
from finagent.domain.results import Unknown
from finagent.domain.types import DomainModel
from finagent.tools.schemas import (
    AuthorityRecord,
    HistoryRecord,
    InvoiceDocument,
    PurchaseOrder,
    Quantity,
    RetrievalItem,
    VendorRecord,
)


class GoodsReceiptLine(DomainModel):
    po_line_ref: str = Field(min_length=1)
    qty: Quantity


class GoodsReceiptNote(DomainModel):
    source_id: str = "CASE:goods_receipt_note"
    lines: tuple[GoodsReceiptLine, ...] = ()


class EvidenceDocument(DomainModel):
    ref: str = Field(min_length=1)
    origin: Literal["supplier", "internal"]
    text: str


class CaseEvidence(DomainModel):
    goods_receipt_note: GoodsReceiptNote | None = None
    documents: tuple[EvidenceDocument, ...] = ()


class Case(DomainModel):
    float_fields: ClassVar[frozenset[str]] = frozenset({"retrieved_chunks"})  # relevance scores only

    request: ProcessingRequest
    # None means the read tool failed after its retries: controls report unknown and hold (plan 3).
    invoice: InvoiceDocument | None = None
    vendor: VendorRecord | None = None
    po: PurchaseOrder | None = None
    history: tuple[HistoryRecord, ...] = ()
    authority: tuple[AuthorityRecord, ...] = ()
    evidence: CaseEvidence = CaseEvidence()
    as_of: AwareDatetime
    unknowns: tuple[Unknown, ...] = ()
    retrieved_chunks: tuple[RetrievalItem, ...] = ()
    authority_source: str | None = None
