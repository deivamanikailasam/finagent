"""BM25 index over chunks. Filtering and lanes belong to the retrieval tool (WP 3.2)."""

import math
from collections import Counter
from collections.abc import Sequence

import bm25s
import numpy as np

from finagent.rag.chunker import Chunk
from finagent.rag.tokenizer import tokenize


class BM25Index:
    def __init__(self, chunks: Sequence[Chunk], use_prefix: bool = True) -> None:
        if not chunks:
            raise ValueError("cannot index an empty corpus")
        self.chunks = tuple(chunks)
        self.use_prefix = use_prefix
        corpus = [tokenize(chunk.prefixed_text if use_prefix else chunk.text) for chunk in self.chunks]
        self._bm25 = bm25s.BM25()
        self._bm25.index(corpus, show_progress=False)
        self._terms = {chunk.chunk_id: set(tokens) for chunk, tokens in zip(self.chunks, corpus, strict=True)}
        self._doc_freq = Counter(term for terms in self._terms.values() for term in terms)
        self._max_idf = max(self._idf_for(freq) for freq in self._doc_freq.values())

    def _idf_for(self, doc_freq: int) -> float:
        return math.log(1 + (len(self.chunks) - doc_freq + 0.5) / (doc_freq + 0.5))

    def idf(self, token: str) -> float:
        """Inverse document frequency. A term outside the corpus counts at the corpus maximum."""
        freq = self._doc_freq.get(token)
        return self._idf_for(freq) if freq else self._max_idf

    def coverage(self, query_tokens: list[str], chunk: Chunk) -> float:
        """Share of the query's IDF weight that this chunk matches (decision log IX)."""
        terms = set(query_tokens)
        total = sum(self.idf(term) for term in terms)
        if total == 0:
            return 0.0
        return sum(self.idf(term) for term in terms & self._terms[chunk.chunk_id]) / total

    def retrieve(
        self, query_tokens: list[str], k: int, weight_mask: np.ndarray | None = None
    ) -> list[tuple[Chunk, float]]:
        """Return up to k (chunk, score) pairs. A mask of zeros excludes chunks before scoring."""
        if weight_mask is not None and len(weight_mask) != len(self.chunks):
            # A misaligned mask could expose a chunk the caller meant to exclude.
            raise ValueError("weight_mask length must equal the number of chunks")
        known = [token for token in query_tokens if token in self._bm25.vocab_dict]
        if not known or k < 1:
            return []
        indices, scores = self._bm25.retrieve(
            [known], k=min(k, len(self.chunks)), weight_mask=weight_mask, show_progress=False
        )
        return [(self.chunks[int(i)], float(s)) for i, s in zip(indices[0], scores[0], strict=True)]
