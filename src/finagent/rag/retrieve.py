"""The retrieve_finance_documents tool: permission-filtered, trust-labelled lanes (decision log IX)."""

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np

from finagent.domain.enums import DocType, TrustClass
from finagent.obs.log import log_event
from finagent.rag.chunker import Chunk, chunk_document, chunk_documents
from finagent.rag.index import BM25Index
from finagent.rag.loader import Document, IngestResult, ingest, is_authoritative
from finagent.rag.tokenizer import tokenize
from finagent.tools.schemas import (
    Lane,
    RetrievalFilters,
    RetrievalItem,
    RetrieveFinanceDocumentsOutput,
)

LANE_K: dict[Lane, int] = {"authoritative": 5, "case_evidence": 5, "non_authoritative": 2}
MIN_COVERAGE = 0.5
_DOC_TYPES = {
    TrustClass.AUTHORITATIVE: DocType.POLICY,
    TrustClass.SUPERSEDED: DocType.POLICY,
    TrustClass.CASE_EVIDENCE: DocType.CASE_EVIDENCE,
    TrustClass.EXTERNAL_UNVERIFIED: DocType.SUPPLIER_DOCUMENT,
    TrustClass.OUT_OF_SCOPE: DocType.REFERENCE,
}


class RetrievalDenied(PermissionError):
    """The principal may not read this document."""


@dataclass(frozen=True)
class CaseDocument:
    case_id: str
    po_ref: str | None
    ref: str
    origin: str
    text: str


@dataclass(frozen=True)
class FetchedSection:
    doc_id: str
    doc_type: DocType
    version: str
    section: str
    trust_class: TrustClass
    citation: str
    text: str


@dataclass(frozen=True)
class _Entry:
    chunk: Chunk
    doc_type: DocType
    doc_id: str
    citation: str
    case_id: str | None = None
    po_ref: str | None = None


def load_case_documents(cases_dir: Path) -> list[CaseDocument]:
    """Read case evidence by path. A case's documents are only ever visible to that case."""
    documents = []
    for evidence_path in sorted(cases_dir.glob("*/evidence.json")):
        directory = evidence_path.parent
        po_path = directory / "po.json"
        po = json.loads(po_path.read_text(encoding="utf-8")) if po_path.exists() else None
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        for item in evidence["documents"]:
            documents.append(
                CaseDocument(
                    directory.name, po["po_ref"] if po else None, item["ref"], item["origin"], item["text"]
                )
            )
    return documents


def _case_entries(documents: list[CaseDocument]) -> list[_Entry]:
    entries = []
    for item in documents:
        # Only an internal record is case evidence; anything a supplier wrote stays unverified.
        trust = TrustClass.CASE_EVIDENCE if item.origin == "internal" else TrustClass.EXTERNAL_UNVERIFIED
        document = Document(
            doc_id=f"CASE:{item.case_id}:{item.ref}",
            file=f"{item.case_id}/{item.ref}",
            version="case",
            title=item.ref.replace("_", " "),
            trust_class=trust,
            effective=None,
            superseded=None,
            acl=("all",),
            restricted=False,
            sha256=hashlib.sha256(item.text.encode()).hexdigest(),
            text=item.text,
        )
        public_id = f"CASE:{item.ref}"
        for chunk in chunk_document(document):
            entries.append(_Entry(chunk, _DOC_TYPES[trust], public_id, public_id, item.case_id, item.po_ref))
    return entries


class Retriever:
    def __init__(self, result: IngestResult, case_documents: list[CaseDocument] | None = None) -> None:
        self._result = result
        corpus = [
            _Entry(chunk, _DOC_TYPES[chunk.trust_class], chunk.doc_id, chunk.chunk_id)
            for chunk in chunk_documents(result.documents)
        ]
        self._entries = [*corpus, *_case_entries(case_documents or [])]
        self._by_chunk = {entry.chunk.chunk_id: entry for entry in self._entries}
        self.index = BM25Index([entry.chunk for entry in self._entries], use_prefix=True)

    @staticmethod
    def _allowed(chunk: Chunk, role: str) -> bool:
        return "all" in chunk.acl or role in chunk.acl

    def _visible(self, entry: _Entry, filters: RetrievalFilters, role: str) -> bool:
        if not self._allowed(entry.chunk, role):
            return False
        if filters.doc_type is not None and entry.doc_type != filters.doc_type:
            return False
        if entry.case_id is not None and entry.case_id != filters.case_id:
            return False
        return not (entry.case_id is not None and filters.po_ref and entry.po_ref != filters.po_ref)

    def _lane(self, entry: _Entry, as_of: datetime) -> Lane:
        if entry.chunk.trust_class == TrustClass.CASE_EVIDENCE:
            return "case_evidence"
        if is_authoritative(self._result, entry.doc_id, as_of):
            return "authoritative"
        return "non_authoritative"  # includes a policy that is not yet effective at as_of

    def _item(self, entry: _Entry, score: float, lane: Lane) -> RetrievalItem:
        chunk = entry.chunk
        return RetrievalItem(
            doc_id=entry.doc_id,
            doc_type=entry.doc_type,
            version=chunk.version,
            section=chunk.section,
            score=score,
            lane=lane,
            trust_class=chunk.trust_class,
            citation=entry.citation,
            text=chunk.text,
        )

    def retrieve_finance_documents(
        self, query: str, filters: RetrievalFilters | None, principal_role: str, as_of: datetime
    ) -> RetrieveFinanceDocumentsOutput:
        started = time.perf_counter()
        filters = filters or RetrievalFilters()
        tokens = tokenize(query)
        visible = [self._visible(entry, filters, principal_role) for entry in self._entries]
        mask = np.array(visible, dtype=float)  # permissions apply before scoring
        scored = self.index.retrieve(tokens, k=len(self._entries), weight_mask=mask)
        visible_ids = {
            entry.chunk.chunk_id for entry, shown in zip(self._entries, visible, strict=True) if shown
        }
        ranked = sorted(
            ((chunk, score) for chunk, score in scored if score > 0 and chunk.chunk_id in visible_ids),
            key=lambda pair: (-pair[1], pair[0].chunk_id),
        )
        lane_candidates: dict[Lane, int] = dict.fromkeys(LANE_K, 0)
        for entry, shown in zip(self._entries, visible, strict=True):
            if shown:
                lane_candidates[self._lane(entry, as_of)] += 1
        lanes: dict[Lane, list[RetrievalItem]] = {lane: [] for lane in LANE_K}
        tops: dict[Lane, Chunk] = {}
        for chunk, score in ranked:
            entry = self._by_chunk[chunk.chunk_id]
            lane = self._lane(entry, as_of)
            tops.setdefault(lane, chunk)
            if len(lanes[lane]) < LANE_K[lane]:
                lanes[lane].append(self._item(entry, score, lane))
        abstained: dict[Lane, bool] = {}
        reasons: dict[Lane, str] = {}
        for lane in LANE_K:
            reason = self._abstain_reason(lane_candidates[lane], tops.get(lane), tokens)
            abstained[lane] = reason is not None
            if reason is not None:
                reasons[lane] = reason
                lanes[lane] = []
        output = RetrieveFinanceDocumentsOutput(
            lanes={lane: tuple(items) for lane, items in lanes.items()},
            abstained=abstained,
            abstain_reason=reasons,
        )
        self._audit(query, principal_role, output, started)
        return output

    def _abstain_reason(self, candidates: int, top: Chunk | None, tokens: list[str]) -> str | None:
        if candidates == 0:
            return "no_candidate"
        if top is None:
            return "non_positive"
        return "low_coverage" if self.index.coverage(tokens, top) < MIN_COVERAGE else None

    def _audit(self, query: str, role: str, output: RetrieveFinanceDocumentsOutput, started: float) -> None:
        for items in output.lanes.values():
            for item in items:
                if self._is_restricted(item.citation):
                    log_event("restricted_access", outcome="ok", doc_id=item.doc_id, principal_role=role)
        log_event(
            "retrieval.query",
            outcome="ok",
            duration_ms=(time.perf_counter() - started) * 1000,
            query_hash=hashlib.sha256(query.encode()).hexdigest(),
            principal_role=role,
            authoritative_count=len(output.lanes["authoritative"]),
            case_evidence_count=len(output.lanes["case_evidence"]),
            non_authoritative_count=len(output.lanes["non_authoritative"]),
            abstained_lanes=",".join(lane for lane, value in output.abstained.items() if value),
        )

    def _is_restricted(self, citation: str) -> bool:
        entry = self._by_chunk.get(citation)
        return entry is not None and entry.chunk.restricted

    def fetch_by_id(
        self, citation: str, principal_role: str = "agent", case_id: str | None = None
    ) -> FetchedSection | None:
        """Fetch one section by citation under the same permissions as a search."""
        key = (
            f"CASE:{case_id}:{citation.removeprefix('CASE:')}#s0"
            if citation.startswith("CASE:")
            else citation
        )
        entry = self._by_chunk.get(key)
        if entry is None:
            return None
        if not self._allowed(entry.chunk, principal_role):
            log_event(
                "restricted_denied", outcome="denied", doc_id=entry.doc_id, principal_role=principal_role
            )
            raise RetrievalDenied(f"{principal_role} may not read {entry.doc_id}")
        if entry.chunk.restricted:
            log_event("restricted_access", outcome="ok", doc_id=entry.doc_id, principal_role=principal_role)
        chunk = entry.chunk
        return FetchedSection(
            entry.doc_id,
            entry.doc_type,
            chunk.version,
            chunk.section,
            chunk.trust_class,
            entry.citation,
            chunk.text,
        )


@lru_cache(maxsize=1)
def default_retriever() -> Retriever:
    result = ingest(Path("corpus"), Path("config/registry.yaml"))
    return Retriever(result, load_case_documents(Path("fixtures/cases")))


def retrieve_finance_documents(
    query: str, filters: RetrievalFilters | None, principal_role: str, as_of: datetime
) -> RetrieveFinanceDocumentsOutput:
    return default_retriever().retrieve_finance_documents(query, filters, principal_role, as_of)


def fetch_by_id(
    citation: str, principal_role: str = "agent", case_id: str | None = None
) -> FetchedSection | None:
    return default_retriever().fetch_by_id(citation, principal_role, case_id)
