"""Optional sentence-transformers wrapper for semantic clustering & similarity.

This is intentionally lazy: importing this module never imports the heavy
torch/transformers stack. Call ``ensure_model()`` to activate it. If the
package isn't installed, ``available()`` returns False and callers should
fall back to the heuristic path.
"""
from __future__ import annotations

import struct
import numpy as np
from typing import Optional

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384
_model = None  # cached after first load


def available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def ensure_model():
    global _model
    if _model is not None:
        return _model
    from sentence_transformers import SentenceTransformer  # heavy, lazy
    _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed(text: str) -> Optional[bytes]:
    if not text or not available():
        return None
    model = ensure_model()
    vec: np.ndarray = model.encode(text, normalize_embeddings=True).astype(np.float32)
    return vec.tobytes()


def to_array(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def cosine(a: bytes, b: bytes) -> float:
    """Both vectors are L2-normalized at encode time, so dot-product == cosine."""
    return float(np.dot(to_array(a), to_array(b)))
