"""BM25 index over chunks. Filtering and lanes belong to the retrieval tool (WP 3.2)."""

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
