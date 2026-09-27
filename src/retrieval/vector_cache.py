from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Optional, Sequence

import numpy as np

from src.models import Chunk
from src.retrieval.embedder import CACHE_PATH


@lru_cache(maxsize=1)
def _load() -> Dict[str, np.ndarray]:
    if not CACHE_PATH.exists():
        return {}
    data = np.load(CACHE_PATH, allow_pickle=True)
    ids = [str(x) for x in data["chunk_ids"]]
    vectors = np.asarray(data["vectors"], dtype=np.float32)
    return {chunk_id: vectors[i] for i, chunk_id in enumerate(ids)}


def load_vector_table() -> Dict[str, np.ndarray]:
    return _load()


def vectors_for(chunk_ids: Sequence[str]) -> Dict[str, np.ndarray]:
    table = _load()
    return {cid: table[cid] for cid in chunk_ids if cid in table}


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 1.0
    return float(1.0 - np.dot(a, b) / denom)


__all__ = [
    "cosine_distance",
    "load_vector_table",
    "vectors_for",
]
