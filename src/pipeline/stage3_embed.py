from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from src.config import AppConfig, load_config
from src.models import Chunk, Stage3Result, Stage3Trace
from src.retrieval.embedder import (
    CACHE_PATH,
    assert_no_truncation,
    embed_chunks,
    get_embedder,
    load_or_compute_cache,
    token_budget,
)


def run_stage3(
    chunks: Sequence[Chunk], config: Optional[AppConfig] = None
) -> Stage3Result:
    config = config or load_config()
    model = get_embedder()
    assert_no_truncation(chunks, model.tokenizer, config)
    vectors, cache_hit = load_or_compute_cache(chunks, config)
    if vectors.shape != (len(chunks), config.embed.dim):
        raise ValueError(
            f"expected {(len(chunks), config.embed.dim)} got {vectors.shape}"
        )
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-3):
        raise ValueError(
            f"embeddings are not unit norm (min={norms.min():.4f} "
            f"max={norms.max():.4f}); normalize_embeddings must stay True"
        )
    return Stage3Result(
        vectors=vectors,
        model_id=config.embed.model_id,
        dim=config.embed.dim,
        truncation_assert_passed=True,
        cache_hit=cache_hit,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stage3_embed", description="Run Stage 3 embedding and the truncation assert"
    )
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(argv)

    from src.pipeline.stage1_load import load_all
    from src.pipeline.stage2_chunk import chunk_documents

    config = load_config()
    documents = load_all(config)
    chunks, decision, _ = chunk_documents(documents, config)
    started = datetime.now()
    model = get_embedder()

    if args.no_cache:
        assert_no_truncation(chunks, model.tokenizer, config)
        vectors = embed_chunks(chunks, config)
        cache_hit = False
        result = Stage3Result(
            vectors=vectors,
            model_id=config.embed.model_id,
            dim=config.embed.dim,
            truncation_assert_passed=True,
            cache_hit=False,
        )
    else:
        result = run_stage3(chunks, config)
    duration = (datetime.now() - started).total_seconds()

    norms = np.linalg.norm(result.vectors, axis=1)
    token_lengths = [len(model.tokenizer(c.embed_input)["input_ids"]) for c in chunks]
    print(f"model_id={result.model_id} max_seq_length={model.max_seq_length}")
    print(f"vectors={result.vectors.shape} dtype={result.vectors.dtype}")
    print(f"dim={result.dim} token_budget={token_budget(config)}")
    print(f"truncation_assert_passed={result.truncation_assert_passed}")
    print(f"cache_hit={result.cache_hit}")
    print(f"unit_norm={bool(np.allclose(norms, 1.0, atol=1e-3))} range=({norms.min():.5f},{norms.max():.5f})")
    print(f"wordpiece_tokens max={max(token_lengths)} min={min(token_lengths)} mean={sum(token_lengths)/len(token_lengths):.1f}")
    print(f"chunks={len(chunks)} duration_s={round(duration,2)}")
    print(f"cache={CACHE_PATH} exists={CACHE_PATH.exists()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
