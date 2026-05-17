"""Sentence-transformer embedding wrapper — model loaded once on first use."""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"

_model = None


def get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Run: uv add sentence-transformers"
            ) from exc
        logger.info("Loading embedding model %s (downloads ~90 MB on first use)", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(text: str) -> np.ndarray:
    """Return a normalized float32 embedding vector for text."""
    return get_model().encode(text, normalize_embeddings=True).astype(np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Dot product of two L2-normalized vectors equals cosine similarity."""
    return float(np.dot(a, b))


def serialize(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def deserialize(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32).copy()
