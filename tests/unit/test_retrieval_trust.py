import io
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from finagent.config.clock import SYDNEY, FixedClock
from finagent.domain.enums import DocType, TrustClass
from finagent.obs.log import configure_logging, logger
from finagent.rag.loader import ingest
from finagent.rag.retrieve import CaseDocument, RetrievalDenied, Retriever, load_case_documents
from finagent.tools.schemas import RetrievalFilters

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "corpus"
REGISTRY = ROOT / "config/registry.yaml"
AS_OF = datetime(2026, 9, 15, 9, 0, tzinfo=SYDNEY)
BANK_QUERY = "bank account change verified vendor master payment instructions"
DFA_QUERY = "delegated financial authority maximum approval cost centre manager limit"
RETRIEVER = Retriever(ingest(CORPUS, REGISTRY), load_case_documents(ROOT / "fixtures/cases"))


@pytest.fixture
def log_stream():
    handlers, level = list(logger.handlers), logger.level
    stream = io.StringIO()
    configure_logging(stream, FixedClock(AS_OF))
    yield stream
    logger.handlers[:] = handlers
    logger.setLevel(level)


def events(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def search(query, role="agent", filters=None, as_of=AS_OF):
    return RETRIEVER.retrieve_finance_documents(query, filters, role, as_of)


def doc_ids(output, lane=None):
    lanes = [lane] if lane else list(output.lanes)
    return {item.doc_id for name in lanes for item in output.lanes[name]}


def test_read_only_role_never_gets_the_restricted_policy(log_stream):
    assert "FIN-POL-004" not in doc_ids(search(BANK_QUERY, role="read_only"))
    assert not [event for event in events(log_stream) if event["event"] == "restricted_access"]


def test_unknown_role_never_gets_the_restricted_policy():
    assert "FIN-POL-004" not in doc_ids(search(BANK_QUERY, role="intruder"))


def test_agent_gets_the_restricted_policy_and_access_is_logged(log_stream):
    assert "FIN-POL-004" in doc_ids(search(BANK_QUERY), "authoritative")
    access = [event for event in events(log_stream) if event["event"] == "restricted_access"]
    assert access and access[0]["doc_id"] == "FIN-POL-004"
    assert access[0]["principal_role"] == "agent"


def test_acl_applies_before_scoring():
    allowed = search(BANK_QUERY, role="read_only")
    assert len(allowed.lanes["authoritative"]) == 5
    assert all(item.doc_id != "FIN-POL-004" for item in allowed.lanes["authoritative"])


def test_fetch_by_id_enforces_the_acl(log_stream):
    with pytest.raises(RetrievalDenied):
        RETRIEVER.fetch_by_id("FIN-POL-004#s2", principal_role="read_only")
    assert any(event["event"] == "restricted_denied" for event in events(log_stream))
    assert RETRIEVER.fetch_by_id("FIN-POL-004#s2", principal_role="agent").section == "s2"
    assert any(event["event"] == "restricted_access" for event in events(log_stream))


def test_bank_query_puts_the_injection_only_in_non_authoritative():
    output = search("urgent payment update new bank account supplier instructions")
    assert "ADV-001" not in doc_ids(output, "authoritative")
    item = next(item for item in output.lanes["non_authoritative"] if item.doc_id == "ADV-001")
    assert item.trust_class == TrustClass.EXTERNAL_UNVERIFIED
    assert item.lane == "non_authoritative"


def test_dfa_query_never_has_the_old_matrix_in_authoritative():
    output = search(DFA_QUERY)
    assert "FIN-POL-003" in doc_ids(output, "authoritative")
    assert "FIN-POL-003-OLD" not in doc_ids(output, "authoritative")
    assert "FIN-POL-003-OLD" in doc_ids(output, "non_authoritative")


def test_policy_not_yet_effective_is_not_authoritative():
    output = search(DFA_QUERY, as_of=datetime(2026, 7, 31, 9, 0, tzinfo=SYDNEY))
    assert "FIN-POL-003" not in doc_ids(output, "authoritative")


def test_old_matrix_is_not_authoritative_before_the_new_one_took_effect():
    output = search(DFA_QUERY, as_of=datetime(2026, 7, 31, 9, 0, tzinfo=SYDNEY))
    assert "FIN-POL-003-OLD" not in doc_ids(output, "authoritative")


def test_travel_extract_is_never_authoritative():
    output = search("dinner meal allowance travel limit AUD 75")
    assert "ADV-002" not in doc_ids(output, "authoritative")


def test_unregistered_current_looking_file_is_untrusted(tmp_path):
    corpus = tmp_path / "corpus"
    shutil.copytree(CORPUS, corpus)
    (corpus / "dfa_v5.md").write_text(
        "---\ndocument_id: FIN-POL-003\nversion: 5.0\nstatus: current\n---\n\n"
        "# Delegated Financial Authority Matrix\n\n## 1. Limits\n\n"
        "delegated financial authority maximum approval cost centre manager limit\n",
        encoding="utf-8",
    )
    output = Retriever(ingest(corpus, REGISTRY)).retrieve_finance_documents(DFA_QUERY, None, "agent", AS_OF)
    assert not any("dfa_v5" in item.doc_id for item in output.lanes["authoritative"])


def test_case_evidence_is_scoped_to_its_case():
    query = "goods receipt note quantity received"
    assert "CASE:goods_receipt_note" not in doc_ids(search(query))
    filters = RetrievalFilters(case_id="FIN-001")
    output = search(query, filters=filters)
    item = output.lanes["case_evidence"][0]
    assert (item.doc_id, item.citation, item.trust_class) == (
        "CASE:goods_receipt_note",
        "CASE:goods_receipt_note",
        TrustClass.CASE_EVIDENCE,
    )
    assert "PO-4471" in item.text


def test_another_cases_evidence_is_not_returned():
    output = search("goods receipt note quantity received", filters=RetrievalFilters(case_id="FIN-005"))
    assert all("PO-4471" not in item.text for item in output.lanes["case_evidence"])


def test_po_filter_excludes_another_pos_evidence():
    filters = RetrievalFilters(case_id="FIN-001", po_ref="PO-9999")
    assert search("goods receipt note quantity received", filters=filters).abstained["case_evidence"]


def test_fin_004_receipt_query_abstains():
    output = search("goods receipt note quantity received", filters=RetrievalFilters(case_id="FIN-004"))
    assert output.abstained["case_evidence"] is True
    assert output.abstain_reason["case_evidence"] == "no_candidate"


def test_supplier_written_note_is_not_case_evidence():
    retriever = Retriever(
        ingest(CORPUS, REGISTRY),
        [
            CaseDocument(
                "FIN-001", "PO-4471", "delivery_note", "supplier", "# Note\n\ngoods receipt delivered in full"
            )
        ],
    )
    output = retriever.retrieve_finance_documents(
        "goods receipt delivered", RetrievalFilters(case_id="FIN-001"), "agent", AS_OF
    )
    assert output.lanes["case_evidence"] == ()
    item = next(item for item in output.lanes["non_authoritative"] if item.doc_id == "CASE:delivery_note")
    assert item.trust_class == TrustClass.EXTERNAL_UNVERIFIED


def test_fin_003_attachment_is_non_authoritative():
    filters = RetrievalFilters(case_id="FIN-003")
    output = search("ignore policy release payment immediately bypass approval", filters=filters)
    assert "CASE:supplier_attachment" not in doc_ids(output, "authoritative")
    assert "CASE:supplier_attachment" not in doc_ids(output, "case_evidence")


def test_doc_type_filter_applies():
    output = search(BANK_QUERY, filters=RetrievalFilters(doc_type=DocType.SUPPLIER_DOCUMENT))
    assert output.lanes["authoritative"] == ()
    assert output.abstain_reason["authoritative"] == "no_candidate"
