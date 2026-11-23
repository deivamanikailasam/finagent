import inspect
from dataclasses import replace
from pathlib import Path

import bm25s
import numpy as np
import pytest
import yaml

from finagent.rag.chunker import chunk_document, chunk_documents
from finagent.rag.index import BM25Index
from finagent.rag.loader import ingest
from finagent.rag.tokenizer import tokenize

ROOT = Path(__file__).resolve().parents[2]
RESULT = ingest(ROOT / "corpus", ROOT / "config/registry.yaml")
CHUNKS = chunk_documents(RESULT.documents)
BY_ID = {chunk.chunk_id: chunk for chunk in CHUNKS}


def test_58_chunks_with_unique_ids():
    assert len(CHUNKS) == 58
    assert len(BY_ID) == 58


@pytest.mark.parametrize("doc_id", ["FIN-POL-003-OLD", "ADV-001", "ADV-002"])
def test_files_without_h2_are_one_whole_chunk(doc_id):
    chunks = [chunk for chunk in CHUNKS if chunk.doc_id == doc_id]
    assert [chunk.chunk_id for chunk in chunks] == [f"{doc_id}#s0"]
    assert "document_id:" not in chunks[0].text


def test_dfa_table_stays_whole():
    text = BY_ID["FIN-POL-003#s2"].text
    for row in (
        "Cost Centre Manager",
        "Department Director",
        "Executive Director",
        "Chief Executive Officer",
    ):
        assert row in text
    assert "## 3." not in text


def test_chunks_do_not_overlap():
    assert "Higher-risk" not in BY_ID["FIN-POL-003#s2"].text
    assert BY_ID["FIN-POL-003#s3"].text.startswith("## 3. Higher-risk transactions")


def test_prefixed_text_carries_title_and_heading():
    chunk = BY_ID["FIN-POL-002#s2"]
    assert chunk.prefixed_text.startswith("Three-Way Matching and Tolerances › 2. Tolerances\n")
    assert chunk.prefixed_text.endswith(chunk.text)


def test_refs_are_extracted_without_the_document_itself():
    assert "FIN-POL-003" in BY_ID["FIN-POL-009#s4"].refs
    assert "FIN-POL-009" not in BY_ID["FIN-POL-009#s4"].refs


def test_every_rule_hash_equals_its_chunk_hash():
    rules = yaml.safe_load((ROOT / "spec/policy_rules.yaml").read_text(encoding="utf-8"))["rules"]
    for rule in rules:
        source = rule["source"]
        for item in [source, *source.get("additional_sections", [])]:
            chunk_id = f"{item.get('doc', source['doc'])}#{item['section']}"
            assert BY_ID[chunk_id].section_hash == item["section_hash"], rule["id"]


@pytest.mark.parametrize(
    "body",
    [
        "# T\n\n## 1. One\n\nfirst\n\n## 1. Again\n\nsecond\n",
        "# T\n\n## 1. One\n\nfirst\n\n## Notes\n\nsecond\n",
        "# T\n\n## 01. One\n\nfirst\n\n## 2. Two\n\nsecond\n",
        "# T\n\n## 1.x One\n\nfirst\n\n## 2. Two\n\nsecond\n",
        "# T\n\n## ١. One\n\nfirst\n\n## 2. Two\n\nsecond\n",
    ],
)
def test_irregular_headings_become_one_chunk_so_no_text_is_lost(body):
    document = replace(RESULT.document("ADV-001"), text=body)
    chunks = chunk_document(document)
    assert [chunk.section for chunk in chunks] == ["s0"]
    assert "first" in chunks[0].text and "second" in chunks[0].text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("variance of 1%", "1%"),
        ("AUD 10,000", "10000"),
        ("AUD 1,000,000", "1000000"),
        ("See FIN-POL-003 today", "fin-pol-003"),
        ("FIN-POL-003-OLD", "fin-pol-003-old"),
        ("invoice INV-1001", "inv-1001"),
        ("order PO-4471", "po-4471"),
        ("the PO permits freight", "permits"),
        ("40 x 150.00", "x"),
        ("40 x 150.00", "150.00"),
        ("APPROVE_FOR_POSTING", "approve_for_posting"),
    ],
)
def test_tokenizer_keeps(text, expected):
    assert expected in tokenize(text)


def test_tokenizer_drops_stopwords_and_does_not_stem():
    assert tokenize("The vendor is on the invoices") == ["vendor", "invoices"]


def test_tokenizer_splits_ordinary_hyphenated_words():
    assert tokenize("three-way") == ["three", "way"]


def test_tokenizer_normalises_look_alike_characters():
    assert tokenize("INV‐1001") == ["inv-1001"]
    assert tokenize("INV-​1001") == ["inv-1001"]


def test_bm25s_signatures_are_pinned():
    index = inspect.signature(bm25s.BM25.index).parameters
    retrieve = inspect.signature(bm25s.BM25.retrieve).parameters
    assert list(index)[:3] == ["self", "corpus", "create_empty_token"]
    assert "show_progress" in index
    for name in ("query_tokens", "k", "show_progress", "weight_mask"):
        assert name in retrieve


def test_retrieve_finds_the_tolerance_section():
    index = BM25Index(CHUNKS)
    results = index.retrieve(tokenize("freight tolerance PO permits"), k=5)
    assert "FIN-POL-002#s2" in [chunk.chunk_id for chunk, _ in results]
    scores = [score for _, score in results]
    assert scores == sorted(scores, reverse=True)
    assert all(isinstance(score, float) for score in scores)


def test_weight_mask_excludes_chunks_before_scoring():
    index = BM25Index(CHUNKS)
    mask = np.array([0 if chunk.doc_id == "FIN-POL-002" else 1 for chunk in CHUNKS], dtype=float)
    results = index.retrieve(tokenize("freight tolerance PO permits"), k=len(CHUNKS), weight_mask=mask)
    assert results and all(chunk.doc_id != "FIN-POL-002" for chunk, _ in results)


def test_misaligned_mask_is_rejected():
    with pytest.raises(ValueError):
        BM25Index(CHUNKS).retrieve(["freight"], k=5, weight_mask=np.ones(3))


def test_k_is_capped_at_the_corpus_size():
    assert 0 < len(BM25Index(CHUNKS).retrieve(["freight"], k=500)) < 58


@pytest.mark.parametrize("tokens", [[], ["zzzunknownterm"]])
def test_empty_or_unknown_query_returns_nothing(tokens):
    assert BM25Index(CHUNKS).retrieve(tokens, k=5) == []


def test_prefix_flag_changes_what_is_indexed():
    query = tokenize("Three-Way Matching")
    with_prefix = BM25Index(CHUNKS, use_prefix=True).retrieve(query, k=5)
    without_prefix = BM25Index(CHUNKS, use_prefix=False).retrieve(query, k=5)
    assert [score for _, score in with_prefix] != [score for _, score in without_prefix]


def test_empty_corpus_is_rejected():
    with pytest.raises(ValueError):
        BM25Index([])
