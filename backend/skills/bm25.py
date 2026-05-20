"""Tiny pure-Python BM25 scorer.

We don't pull in `rank_bm25` (another wheel, another vendor) — BM25
fits in 60 lines and stays fully transparent. Used by the hybrid
skill search (Section 7) as the keyword-match half of the score.

Tokens are lowercased + split on word boundaries; we drop ASCII
punctuation but otherwise keep things simple.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


def tokenize(text: str) -> list[str]:
    """Lowercase + split on word boundaries. Hyphens kept inside tokens."""
    return _TOKEN_RE.findall((text or "").lower())


@dataclass
class BM25:
    """In-memory BM25 over a small document set (≤ a few hundred items)."""
    k1: float = 1.5
    b: float = 0.75

    # Built by fit() — don't set manually.
    docs: list[list[str]] = field(default_factory=list)
    doc_freq: Counter = field(default_factory=Counter)
    avgdl: float = 0.0

    def fit(self, documents: Iterable[str]) -> None:
        self.docs = [tokenize(d) for d in documents]
        self.doc_freq = Counter()
        for tokens in self.docs:
            for term in set(tokens):
                self.doc_freq[term] += 1
        total = sum(len(d) for d in self.docs)
        self.avgdl = (total / len(self.docs)) if self.docs else 0.0

    def score(self, query: str) -> list[float]:
        """Return a parallel list of BM25 scores, one per fitted document."""
        q_terms = tokenize(query)
        if not q_terms or not self.docs:
            return [0.0] * len(self.docs)

        N = len(self.docs)
        # Standard Robertson/Sparck-Jones IDF (with the +1 smoothing).
        idf: dict[str, float] = {}
        for term in set(q_terms):
            df = self.doc_freq.get(term, 0)
            idf[term] = math.log(1.0 + (N - df + 0.5) / (df + 0.5))

        scores: list[float] = []
        for tokens in self.docs:
            dl = len(tokens)
            if dl == 0:
                scores.append(0.0)
                continue
            tf = Counter(tokens)
            s = 0.0
            for term in q_terms:
                f = tf.get(term, 0)
                if f == 0:
                    continue
                norm = 1 - self.b + self.b * (dl / self.avgdl if self.avgdl > 0 else 0)
                s += idf[term] * ((f * (self.k1 + 1)) / (f + self.k1 * norm))
            scores.append(s)
        return scores


def normalise(scores: list[float]) -> list[float]:
    """Min-max into 0..1. Empty / all-equal input → all zeros."""
    if not scores:
        return []
    mn = min(scores)
    mx = max(scores)
    if mx == mn:
        return [0.0 for _ in scores]
    return [(s - mn) / (mx - mn) for s in scores]
