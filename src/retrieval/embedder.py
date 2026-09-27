from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.config import PROJECT_ROOT, AppConfig, load_config
from src.models import Chunk, IngestError

CACHE_PATH = PROJECT_ROOT / "data" / "chunks.embeddings.npz"
SAFETY_MARGIN = 24


@lru_cache(maxsize=1)
def get_embedder():
    from sentence_transformers import SentenceTransformer

    config = load_config()
    model = SentenceTransformer(config.embed.model_id, device="cpu")
    model.max_seq_length = config.embed.max_seq_length
    if model.max_seq_length != 256:
        raise IngestError(
            f"{config.embed.model_id} reports max_seq_length="
            f"{model.max_seq_length}, expected 256; refusing to embed because the "
            "no-truncation budget is derived from it"
        )
    return model


def token_budget(config: Optional[AppConfig] = None) -> int:
    config = config or load_config()
    return config.embed.max_seq_length - 2 - SAFETY_MARGIN


TOKEN_BUDGET = token_budget()


def embed_input(chunk: Chunk) -> str:
    text = chunk.embed_input
    if not text or not text.strip():
        raise IngestError(f"{chunk.chunk_id}: embed_input is empty")
    return text


def assert_no_truncation(
    chunks: Sequence[Chunk], tokenizer, config: Optional[AppConfig] = None
) -> None:
    budget = token_budget(config)
    offenders: List[Tuple[str, int]] = []
    for chunk in chunks:
        text = embed_input(chunk)
        n_tokens = len(tokenizer(text)["input_ids"])
        if n_tokens > budget:
            offenders.append((chunk.chunk_id, n_tokens))
    if offenders:
        detail = ", ".join(f"{cid}={n}" for cid, n in offenders[:10])
        raise IngestError(
            f"{len(offenders)} chunk(s) exceed the {budget}-token embedding budget "
            f"and MiniLM would truncate them silently: {detail}"
        )


def embed_chunks(
    chunks: Sequence[Chunk], config: Optional[AppConfig] = None
) -> np.ndarray:
    config = config or load_config()
    model = get_embedder()
    if config.embed.assert_no_truncation:
        assert_no_truncation(chunks, model.tokenizer, config)
    texts = [embed_input(chunk) for chunk in chunks]
    vectors = model.encode(
        texts,
        batch_size=config.embed.batch_size,
        normalize_embeddings=config.embed.normalize,
        convert_to_numpy=True,
        truncate_dim=None,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


def embed_query(text: str, config: Optional[AppConfig] = None) -> np.ndarray:
    config = config or load_config()
    model = get_embedder()
    if config.embed.assert_no_truncation:
        n_tokens = len(model.tokenizer(text)["input_ids"])
        budget = token_budget(config)
        if n_tokens > budget:
            raise IngestError(
                f"query is {n_tokens} tokens, over the {budget}-token budget"
            )
    vector = model.encode(
        [text],
        normalize_embeddings=config.embed.normalize,
        convert_to_numpy=True,
        truncate_dim=None,
        show_progress_bar=False,
    )
    return np.asarray(vector[0], dtype=np.float32)


def corpus_fingerprint(chunks: Sequence[Chunk], model_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(model_id.encode("utf-8"))
    for chunk in sorted(chunks, key=lambda c: c.chunk_id):
        digest.update(chunk.chunk_id.encode("utf-8"))
        digest.update(chunk.embed_input.encode("utf-8"))
    return digest.hexdigest()


def _save_cache(
    path: Path, chunks: Sequence[Chunk], vectors: np.ndarray, model_id: str, dim: int
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        chunk_ids=np.array([chunk.chunk_id for chunk in chunks], dtype=object),
        vectors=vectors,
        model_id=np.array([model_id]),
        dim=np.array([dim]),
        fingerprint=np.array([corpus_fingerprint(chunks, model_id)]),
    )


def _load_cache(
    path: Path, chunks: Sequence[Chunk], model_id: str, dim: int
) -> Optional[np.ndarray]:
    if not path.exists():
        return None
    try:
        data = np.load(path, allow_pickle=True)
    except Exception:
        return None
    try:
        if str(data["model_id"][0]) != model_id:
            return None
        if int(data["dim"][0]) != dim:
            return None
        cached_ids = [str(x) for x in data["chunk_ids"]]
        if cached_ids != [chunk.chunk_id for chunk in chunks]:
            return None
        if str(data["fingerprint"][0]) != corpus_fingerprint(chunks, model_id):
            return None
        vectors = np.asarray(data["vectors"], dtype=np.float32)
    except (KeyError, IndexError, ValueError):
        return None
    if vectors.shape != (len(chunks), dim):
        return None
    return vectors


def load_or_compute_cache(
    chunks: Sequence[Chunk], config: Optional[AppConfig] = None
) -> Tuple[np.ndarray, bool]:
    config = config or load_config()
    cached = _load_cache(CACHE_PATH, chunks, config.embed.model_id, config.embed.dim)
    if cached is not None:
        return cached, True
    vectors = embed_chunks(chunks, config)
    _save_cache(CACHE_PATH, chunks, vectors, config.embed.model_id, config.embed.dim)
    return vectors, False


__all__ = [
    "CACHE_PATH",
    "TOKEN_BUDGET",
    "assert_no_truncation",
    "corpus_fingerprint",
    "embed_chunks",
    "embed_input",
    "embed_query",
    "get_embedder",
    "load_or_compute_cache",
    "token_budget",
]
