"""The retrieve_finance_documents tool: permission-scoped, trust-labelled lanes (decision log IX)."""

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from finagent.domain.enums import DocType, TrustClass
from finagent.obs.log import log_event
from finagent.rag.chunker import Chunk, chunk_document, chunk_documents
from finagent.rag.index import BM25Index
from finagent.rag.loader import Document, IngestResult, is_authoritative, load_verified
from finagent.rag.tokenizer import tokenize
from finagent.tools.schemas import (
    Lane,
    RetrievalFilters,
    RetrievalItem,
    RetrieveFinanceDocumentsOutput,
)

LANE_K: dict[Lane, int] = {"authoritative": 5, "case_evidence": 5, "non_authoritative": 2}
MIN_COVERAGE = 0.5
KNOWN_ROLES = frozenset({"agent", "approver", "approver_financial_control", "read_only"})
_CASE_ACL = ("agent", "approver", "approver_financial_control")
_CASE_ID = re.compile(r"[A-Z]{3}-[0-9]{3}[a-z]?")
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
    lane: Lane
    citation: str
    text: str
    restricted: bool = False


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
            acl=_CASE_ACL,
            restricted=False,
            sha256=hashlib.sha256(item.text.encode()).hexdigest(),
            text=item.text,
        )
        public_id = f"CASE:{item.ref}"
        for chunk in chunk_document(document):
            # The signed-off expectations cite a single-section note as CASE:<ref>.
            citation = public_id if chunk.section == "s0" else f"{public_id}#{chunk.section}"
            entries.append(_Entry(chunk, _DOC_TYPES[trust], public_id, citation, item.case_id, item.po_ref))
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
        self.index = BM25Index([entry.chunk for entry in self._entries], use_prefix=True)  # evaluation only
        self._scoped: dict[tuple[str, ...], BM25Index] = {}

    def is_in_force(self, doc_id: str, as_of: datetime) -> bool:
        return is_authoritative(self._result, doc_id, as_of)

    @staticmethod
    def _allowed(chunk: Chunk, role: str) -> bool:
        return role in KNOWN_ROLES and ("all" in chunk.acl or role in chunk.acl)

    def _visible(self, entry: _Entry, filters: RetrievalFilters, role: str, as_of: datetime) -> bool:
        if not self._allowed(entry.chunk, role):
            return False
        if filters.doc_type is not None and entry.doc_type != filters.doc_type:
            return False
        if entry.case_id is not None and entry.case_id != filters.case_id:
            return False
        if entry.case_id is not None and filters.po_ref and entry.po_ref != filters.po_ref:
            return False
        # A policy outside its validity dates is not offered at all, so it cannot be cited.
        return entry.chunk.trust_class != TrustClass.AUTHORITATIVE or self.is_in_force(entry.doc_id, as_of)

    def _lane(self, entry: _Entry, as_of: datetime) -> Lane:
        if entry.chunk.trust_class == TrustClass.CASE_EVIDENCE:
            return "case_evidence"
        return "authoritative" if self.is_in_force(entry.doc_id, as_of) else "non_authoritative"

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

    def _scope_index(self, entries: list[_Entry]) -> BM25Index:
        # Statistics come only from what this caller may see in this lane, so hidden or untrusted
        # documents cannot change a score, a coverage value or an abstention.
        key = tuple(entry.chunk.chunk_id for entry in entries)
        if key not in self._scoped:
            self._scoped[key] = BM25Index([entry.chunk for entry in entries], use_prefix=True)
        return self._scoped[key]

    def _lane_entries(
        self, filters: RetrievalFilters, role: str, as_of: datetime
    ) -> dict[Lane, list[_Entry]]:
        lanes: dict[Lane, list[_Entry]] = {lane: [] for lane in LANE_K}
        for entry in self._entries:
            if self._visible(entry, filters, role, as_of):
                lanes[self._lane(entry, as_of)].append(entry)
        return lanes

    def _require_known_role(self, role: str) -> None:
        if role not in KNOWN_ROLES:
            log_event("retrieval_denied", outcome="denied", duration_ms=0.0, reason_code="unknown_role")
            raise RetrievalDenied("unknown principal role")

    def lane_ranking(
        self, query: str, filters: RetrievalFilters | None, role: str, as_of: datetime, lane: Lane
    ) -> list[str]:
        """Citations in rank order before abstention, for evaluation reporting."""
        self._require_known_role(role)
        entries = self._lane_entries(filters or RetrievalFilters(), role, as_of)[lane]
        if not entries:
            return []
        scored = self._scope_index(entries).retrieve(tokenize(query), k=len(entries))
        return [self._by_chunk[chunk.chunk_id].citation for chunk, _ in scored]

    def retrieve_finance_documents(
        self, query: str, filters: RetrievalFilters | None, principal_role: str, as_of: datetime
    ) -> RetrieveFinanceDocumentsOutput:
        started = time.perf_counter()
        self._require_known_role(principal_role)
        tokens = tokenize(query)
        lane_entries = self._lane_entries(filters or RetrievalFilters(), principal_role, as_of)
        lanes: dict[Lane, tuple[RetrievalItem, ...]] = {}
        abstained: dict[Lane, bool] = {}
        reasons: dict[Lane, str] = {}
        restricted: list[str] = []
        for lane, entries in lane_entries.items():
            ranked: list[tuple[Chunk, float]] = []
            index = self._scope_index(entries) if entries else None
            if index is not None:
                ranked = index.retrieve(tokens, k=len(entries))
            reason = self._abstain_reason(index, ranked, tokens)
            abstained[lane] = reason is not None
            if reason is not None:
                reasons[lane] = reason
                lanes[lane] = ()
                continue
            chosen = [(self._by_chunk[chunk.chunk_id], score) for chunk, score in ranked[: LANE_K[lane]]]
            lanes[lane] = tuple(self._item(entry, score, lane) for entry, score in chosen)
            restricted.extend(entry.doc_id for entry, _ in chosen if entry.chunk.restricted)
        output = RetrieveFinanceDocumentsOutput(lanes=lanes, abstained=abstained, abstain_reason=reasons)
        for doc_id in restricted:
            log_event(
                "restricted_access",
                outcome="ok",
                duration_ms=(time.perf_counter() - started) * 1000,
                doc_id=doc_id,
                principal_role=principal_role,
            )
        log_event(
            "retrieval.query",
            outcome="ok",
            duration_ms=(time.perf_counter() - started) * 1000,
            query_hash=hashlib.sha256(query.encode()).hexdigest(),
            principal_role=principal_role,
            authoritative_count=len(output.lanes["authoritative"]),
            case_evidence_count=len(output.lanes["case_evidence"]),
            non_authoritative_count=len(output.lanes["non_authoritative"]),
            abstained_lanes=",".join(lane for lane, value in output.abstained.items() if value),
        )
        return output

    @staticmethod
    def _abstain_reason(
        index: BM25Index | None, ranked: list[tuple[Chunk, float]], tokens: list[str]
    ) -> str | None:
        if index is None:
            return "no_candidate"
        if not ranked:
            return "non_positive"
        return "low_coverage" if index.coverage(tokens, ranked[0][0]) < MIN_COVERAGE else None

    def _resolve(self, citation: str, case_id: str | None) -> _Entry | None:
        if not citation.startswith("CASE:"):
            return self._by_chunk.get(citation)
        if case_id is None or not _CASE_ID.fullmatch(case_id):
            return None  # case evidence is reachable only through its own case
        reference = citation.removeprefix("CASE:")
        key = f"CASE:{case_id}:{reference}" if "#" in reference else f"CASE:{case_id}:{reference}#s0"
        return self._by_chunk.get(key)

    def fetch_by_id(
        self, citation: str, principal_role: str, as_of: datetime, case_id: str | None = None
    ) -> FetchedSection | None:
        """Fetch one section under the same permissions and validity dates as a search."""
        started = time.perf_counter()
        citation_hash = hashlib.sha256(citation.encode()).hexdigest()

        def audit(outcome: str, count: int) -> None:
            log_event(
                "retrieval.fetch",
                outcome=outcome,
                duration_ms=(time.perf_counter() - started) * 1000,
                citation_hash=citation_hash,
                principal_role=principal_role if principal_role in KNOWN_ROLES else "unknown",
                count=count,
            )

        if principal_role not in KNOWN_ROLES:
            audit("denied", 0)
            raise RetrievalDenied("unknown principal role")
        entry = self._resolve(citation, case_id)
        if entry is None:
            audit("not_found", 0)
            return None
        if not self._allowed(entry.chunk, principal_role):
            log_event(
                "restricted_denied",
                outcome="denied",
                duration_ms=(time.perf_counter() - started) * 1000,
                doc_id=entry.doc_id,
                principal_role=principal_role,
            )
            audit("denied", 0)
            raise RetrievalDenied(f"{principal_role} may not read {entry.doc_id}")
        chunk = entry.chunk
        if chunk.trust_class == TrustClass.AUTHORITATIVE and not self.is_in_force(entry.doc_id, as_of):
            audit("not_in_force", 0)  # never hand back a section labelled authoritative outside its dates
            return None
        if chunk.restricted:
            log_event(
                "restricted_access",
                outcome="ok",
                duration_ms=(time.perf_counter() - started) * 1000,
                doc_id=entry.doc_id,
                principal_role=principal_role,
            )
        audit("ok", 1)
        return FetchedSection(
            entry.doc_id,
            entry.doc_type,
            chunk.version,
            chunk.section,
            chunk.trust_class,
            self._lane(entry, as_of),
            entry.citation,
            chunk.text,
            bool(chunk.restricted),
        )


_CACHE: dict[Path, tuple[str, Retriever]] = {}


def default_retriever(root: Path = Path(".")) -> Retriever:
    """Verify the corpus on every call, and rebuild when the verified contents change."""
    root = root.resolve()
    home = Path(os.environ.get("FINAGENT_HOME") or root / "var")
    result = load_verified(root / "corpus", root / "config/registry.yaml", home / "index_manifest.json")
    identity = f"{result.index_hash}:{result.registry_sha256}"
    cached = _CACHE.get(root)
    if cached is None or cached[0] != identity:
        _CACHE[root] = (identity, Retriever(result, load_case_documents(root / "fixtures/cases")))
    return _CACHE[root][1]


def retrieve_finance_documents(
    query: str, filters: RetrievalFilters | None, principal_role: str, as_of: datetime
) -> RetrieveFinanceDocumentsOutput:
    return default_retriever().retrieve_finance_documents(query, filters, principal_role, as_of)


def fetch_by_id(
    citation: str, principal_role: str, as_of: datetime, case_id: str | None = None
) -> FetchedSection | None:
    return default_retriever().fetch_by_id(citation, principal_role, as_of, case_id)
