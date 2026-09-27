from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from src.config import AppConfig, load_config
from src.models import Chunk, Stage4Trace, StoreResult

CHROMA_METADATA_TYPES = (str, int, float, bool)


def get_client(config: Optional[AppConfig] = None):
    import chromadb
    from chromadb.config import Settings

    config = config or load_config()
    return chromadb.PersistentClient(
        path=config.store.path,
        settings=Settings(anonymized_telemetry=False, allow_reset=True),
    )


def get_or_create_collection(client, config: Optional[AppConfig] = None, rebuild: bool = False):
    config = config or load_config()
    if rebuild:
        try:
            client.delete_collection(config.store.collection)
        except Exception:
            pass
    return client.get_or_create_collection(
        name=config.store.collection,
        metadata={"hnsw:space": config.store.metric},
        embedding_function=None,
    )


def _coerce(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, CHROMA_METADATA_TYPES):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def chunk_to_chroma(chunk: Chunk) -> Dict[str, Any]:
    return {
        "id": chunk.chunk_id,
        "document": chunk.text,
        "metadata": {
            "scheme_id": _coerce(chunk.scheme_id),
            "doc_id": _coerce(chunk.doc_id),
            "section": _coerce(chunk.section),
            "field": _coerce(chunk.field),
            "value": _coerce(chunk.value),
            "source_url": _coerce(chunk.source_url),
            "effective_date": _coerce(chunk.effective_date),
            "historical": bool(chunk.historical),
            "is_fact": bool(chunk.is_fact),
            "value_type": _coerce(chunk.value_type),
            "scope": _coerce(chunk.scope),
        },
    }


def store_chunks(
    chunks: Sequence[Chunk],
    config: Optional[AppConfig] = None,
    vectors: Optional[Any] = None,
    rebuild: bool = False,
) -> StoreResult:
    import numpy as np

    config = config or load_config()
    if vectors is None:
        raise ValueError(
            "store_chunks requires the Stage 3 vectors. Omitting them makes Chroma "
            "embed chunk.text with its own bundled model, which silently drops the "
            "scheme-name/category prefix built in Phase 2."
        )
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.shape != (len(chunks), config.embed.dim):
        raise ValueError(
            f"expected vectors of shape {(len(chunks), config.embed.dim)}, "
            f"got {vectors.shape}"
        )
    client = get_client(config)
    collection = get_or_create_collection(client, config, rebuild=rebuild)
    records = [chunk_to_chroma(chunk) for chunk in chunks]
    batch = 200
    for start in range(0, len(records), batch):
        stop = start + batch
        collection.upsert(
            ids=[r["id"] for r in records[start:stop]],
            documents=[r["document"] for r in records[start:stop]],
            metadatas=[r["metadata"] for r in records[start:stop]],
            embeddings=vectors[start:stop].tolist(),
        )
    assert_stored_vectors(collection, records, vectors, config)
    return StoreResult(
        collection=collection.name,
        upserted=len(records),
        total_in_collection=collection.count(),
        metric=config.store.metric,
    )


def assert_stored_vectors(collection, records, vectors, config) -> None:
    """Catch Chroma substituting its own embedding function for the Stage 3 vectors.

    This compares what Chroma persisted against what we handed it, so it detects
    Chroma silently re-embedding ``chunk.text`` with its bundled ONNX model. It
    cannot detect a caller that passes the wrong vectors in the first place --
    ``scripts/verify_pipeline.py`` covers that by cross-checking the store against
    a fresh Stage 3 recompute.
    """
    import numpy as np

    got = collection.get(include=["embeddings"])
    stored = np.asarray(got["embeddings"], dtype=np.float32)
    if stored.shape != vectors.shape:
        raise ValueError(
            f"stored vector shape {stored.shape} != expected {vectors.shape}"
        )
    order = {cid: i for i, cid in enumerate(got["ids"])}
    expected = np.stack([vectors[order[r["id"]]] for r in records])
    delta = float(np.abs(stored - expected).max())
    if delta > 1e-4:
        raise ValueError(
            f"Chroma stored vectors differ from the Stage 3 embeddings by "
            f"{delta:.4f}; the index does not reflect Stage 3 and retrieval is "
            "silently using a different model/prefix"
        )


def build_where(scheme_ids: Optional[Sequence[str]] = None) -> Optional[Dict[str, Any]]:
    if not scheme_ids:
        return None
    return {"scheme_id": {"$in": list(scheme_ids)}}


def query_collection(
    config: Optional[AppConfig],
    query_text: str,
    scheme_ids: Optional[Sequence[str]] = None,
    n_results: int = 6,
) -> Dict[str, Any]:
    from src.retrieval.embedder import embed_query

    config = config or load_config()
    client = get_client(config)
    collection = get_or_create_collection(client, config)
    total = collection.count()
    if total == 0:
        raise RuntimeError(
            f"collection {collection.name!r} is empty. Run: python scripts/ingest.py"
        )
    vector = embed_query(query_text, config).tolist()
    n = min(n_results, total)
    result = collection.query(
        query_embeddings=[vector],
        n_results=n,
        where=build_where(scheme_ids),
        include=["documents", "metadatas", "distances"],
    )
    return {
        "ids": result["ids"],
        "documents": result["documents"],
        "metadatas": result["metadatas"],
        "distances": result["distances"],
        "total_in_collection": total,
    }


def collection_trace(result: StoreResult) -> Stage4Trace:
    return Stage4Trace(
        collection=result.collection, n_chunks=result.total_in_collection, metric=result.metric
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stage4_store", description="Upsert Phase 2 chunks into ChromaDB"
    )
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args(argv)

    from src.observability.runlog import read_jsonl
    from src.pipeline.stage2_chunk import CHUNKS_PATH
    from src.pipeline.stage3_embed import run_stage3
    from src.models import Chunk as ChunkModel

    config = load_config()
    rows = read_jsonl(CHUNKS_PATH)
    chunks = [ChunkModel(**row) for row in rows]
    vectors = run_stage3(chunks, config).vectors
    result = store_chunks(chunks, config, vectors=vectors, rebuild=args.rebuild)
    print(f"collection={result.collection} metric={result.metric}")
    print(f"upserted={result.upserted} total_in_collection={result.total_in_collection}")
    client = get_client(config)
    collection = get_or_create_collection(client, config)
    got = collection.get(include=["metadatas"])
    schemes: Dict[str, int] = {}
    for meta in got["metadatas"]:
        schemes[meta["scheme_id"]] = schemes.get(meta["scheme_id"], 0) + 1
    for scheme_id in sorted(schemes):
        print(f"  {scheme_id:26s} {schemes[scheme_id]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
