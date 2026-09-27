from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.models import RetrievedChunk
from src.retrieval.vector_cache import cosine_distance, vectors_for


def _all(_item: RetrievedChunk) -> bool:
    return True


def base_field(field: str) -> str:
    """Map a chunk field to the current-value field it supersedes.

    ``exit_load_historical`` -> ``exit_load``. Historical chunks carry their own
    field name, so a naive field comparison never matches them against the
    current chunk they supersede.
    """
    suffix = "_historical"
    return field[: -len(suffix)] if field.endswith(suffix) else field


def mmr_rerank(
    query_vec: np.ndarray,
    candidates: Sequence[RetrievedChunk],
    k: int = 4,
    lambda_: float = 0.3,
    vectors: Optional[Dict[str, np.ndarray]] = None,
    max_per_field: Optional[int] = 3,
) -> List[RetrievedChunk]:
    """Maximise (1-lambda)*(1 - d(q, c_i)) - lambda*max_j d(c_i, c_j), greedily.

    d is cosine distance, so 1 - d(q, c_i) is cosine similarity. The second term
    penalises a candidate for resembling something already selected, which is what
    stops three near-identical min_sip chunks from one page crowding out the chunk
    that actually answers the question.

    ``max_per_field`` additionally caps how many selected chunks may share a
    ``field``, and a ``(field, value)`` pair is only ever selected once. Pure MMR
    is not enough here: for the unfiltered question "What is the minimum SIP?" the
    four schemes that all read "Min. for SIP: Rs 100" are mutually redundant, so
    MMR demoted the only distinctive chunk, ELSS at Rs 500, and promoted a weaker
    match instead. Capping per field (default 3, so a 5-scheme question can still
    surface 3 distinct values) and de-duplicating identical ``(field, value)``
    answers keeps the odd one out, which is the information the answer needs.

    Superseded ``exit_load_historical`` chunks are dropped outright whenever a
    current chunk for the same field is in the pool. Without this, a query for
    "the exit load" retrieved three historical rows at a higher similarity than
    the current row, which is precisely the stale-answer failure T4 warns about.
    """
    pool = list(candidates)
    if not pool:
        return []
    current_fields = {
        base_field(item.chunk.field) for item in pool if not item.chunk.historical
    }
    non_superseded = [
        item
        for item in pool
        if not (
            item.chunk.historical and base_field(item.chunk.field) in current_fields
        )
    ]
    if non_superseded:
        pool = non_superseded
    if vectors is None:
        vectors = vectors_for([item.chunk.chunk_id for item in pool])

    def redundancy(item: RetrievedChunk) -> float:
        worst = 0.0
        vec = vectors.get(item.chunk.chunk_id)
        for chosen in selected:
            other = vectors.get(chosen.chunk.chunk_id)
            if vec is None or other is None:
                worst = max(worst, item.cosine_distance)
            else:
                worst = max(worst, cosine_distance(vec, other))
        return worst

    def score(item: RetrievedChunk) -> float:
        q_sim = 1.0 - item.cosine_distance
        return (1.0 - lambda_) * q_sim - lambda_ * redundancy(item)

    def pair(item: RetrievedChunk) -> Tuple[str, str]:
        return (item.chunk.field, str(item.chunk.value))

    def field_ok(item: RetrievedChunk) -> bool:
        if max_per_field is None:
            return True
        return field_counts.get(item.chunk.field, 0) < max_per_field

    def pair_ok(item: RetrievedChunk) -> bool:
        return pair(item) not in seen_pairs

    def strict(item: RetrievedChunk) -> bool:
        return field_ok(item) and pair_ok(item)

    selected: List[RetrievedChunk] = []
    field_counts: Dict[str, int] = {}
    seen_pairs: set = set()
    remaining = list(pool)
    for constraint in (strict, field_ok, pair_ok, _all):
        while remaining and len(selected) < k:
            eligible = [item for item in remaining if constraint(item)]
            if not eligible:
                break
            best_item = max(eligible, key=score)
            selected.append(best_item)
            remaining = [
                item for item in remaining if item.chunk.chunk_id != best_item.chunk.chunk_id
            ]
            field_counts[best_item.chunk.field] = (
                field_counts.get(best_item.chunk.field, 0) + 1
            )
            seen_pairs.add(pair(best_item))
        if len(selected) >= k:
            break
    for rank, item in enumerate(selected, start=1):
        item.rank = rank
    return selected


def similarity_floor_check(
    candidates: Sequence[RetrievedChunk], sim_floor: float
) -> Tuple[bool, float]:
    """Return (grounded, best_similarity). below the floor means not in corpus."""
    if not candidates:
        return False, 0.0
    best = max(1.0 - item.cosine_distance for item in candidates)
    return best >= sim_floor, best


__all__ = ["mmr_rerank", "similarity_floor_check"]
