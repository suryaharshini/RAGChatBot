from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.models import IngestError, Stage1Trace, Stage2Trace, Stage3Trace, Stage4Trace, Trace
from src.observability.runlog import write_ingest_run_log, write_jsonl
from src.pipeline.stage1_load import load_all
from src.pipeline.stage2_chunk import audit_chunks, chunk_documents, select_chunking_strategy
from src.pipeline.stage3_embed import run_stage3
from src.pipeline.stage4_store import collection_trace, store_chunks

DOCUMENTS_PATH = PROJECT_ROOT / "data" / "processed" / "documents.jsonl"
CHUNKS_PATH = PROJECT_ROOT / "data" / "processed" / "chunks.jsonl"


def ingest(
    live: bool = False,
    chunk_strategy: Optional[str] = None,
    rebuild: bool = False,
) -> Dict[str, Any]:
    config = load_config()
    if chunk_strategy:
        config.chunk.strategy = chunk_strategy
    started = datetime.now()
    trace = Trace()

    documents = load_all(config, live=live)
    write_jsonl(DOCUMENTS_PATH, [json.loads(d.model_dump_json()) for d in documents])
    first = documents[0]
    trace.stage1 = Stage1Trace(
        source_url=first.source_url,
        fetched_at=first.fetched_at,
        text_sha256=first.text_sha256,
        chars=len(first.text),
    )

    chunks, decision, disagreements = chunk_documents(documents, config)
    audit = audit_chunks(chunks, config)
    audit["facts_vs_text_disagreements"] = disagreements
    if not audit["passed"]:
        write_ingest_run_log(
            decision=decision,
            stage_summary={"stage1_documents": len(documents), "aborted_at": "stage2_audit"},
            audit=audit,
            duration_s=(datetime.now() - started).total_seconds(),
            extra={"stage": "ingest_aborted"},
        )
        raise IngestError(
            "Stage 2 audit failed, refusing to embed or store: "
            + "; ".join(audit["failures"][:5])
        )
    write_jsonl(CHUNKS_PATH, [json.loads(c.model_dump_json()) for c in chunks])
    trace.stage2 = Stage2Trace(
        chunk_ids=[c.chunk_id for c in chunks],
        token_counts=[c.token_count for c in chunks],
        strategy=decision.to_dict(),
    )

    result3 = run_stage3(chunks, config)
    if not result3.truncation_assert_passed:
        raise IngestError("Stage 3 truncation assertion failed, refusing to store")
    trace.stage3 = Stage3Trace(
        model_id=result3.model_id,
        dim=result3.dim,
        truncation_assert_passed=result3.truncation_assert_passed,
        cache_hit=result3.cache_hit,
    )

    result4 = store_chunks(chunks, config, vectors=result3.vectors, rebuild=rebuild)
    trace.stage4 = collection_trace(result4)

    per_scheme: Dict[str, int] = {}
    for chunk in chunks:
        per_scheme[chunk.scheme_id] = per_scheme.get(chunk.scheme_id, 0) + 1
    duration = (datetime.now() - started).total_seconds()
    stage_summary = {
        "stage1_documents": len(documents),
        "source_urls": [d.source_url for d in documents],
        "stage2_chunks": len(chunks),
        "chunks_per_scheme": per_scheme,
        "stage3_vectors": int(result3.vectors.shape[0]),
        "stage3_dim": result3.dim,
        "stage3_cache_hit": result3.cache_hit,
        "stage4_collection": result4.collection,
        "stage4_total_in_collection": result4.total_in_collection,
        "live": live,
    }
    write_ingest_run_log(
        decision=decision,
        stage_summary=stage_summary,
        audit=audit,
        duration_s=duration,
        extra={"stage": "ingest", "trace": json.loads(trace.model_dump_json())},
    )
    return {
        "trace": trace,
        "decision": decision,
        "audit": audit,
        "stage3": result3,
        "stage4": result4,
        "per_scheme": per_scheme,
        "duration_s": duration,
        "stage_summary_docs": len(documents),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ingest", description="Run Stages 1-4: load, chunk, embed, store"
    )
    parser.add_argument("--live", action="store_true", help="re-fetch instead of using snapshots")
    parser.add_argument(
        "--chunk-strategy", choices=["auto", "label_aware_recursive", "semantic"]
    )
    parser.add_argument("--rebuild", action="store_true", help="drop and recreate the collection")
    args = parser.parse_args(argv)

    outcome = ingest(
        live=args.live, chunk_strategy=args.chunk_strategy, rebuild=args.rebuild
    )
    decision = outcome["decision"]
    result3 = outcome["stage3"]
    result4 = outcome["stage4"]

    print(f"mode={'live' if args.live else 'snapshot'} strategy={decision.selected}")
    print(f"documents={outcome['stage_summary_docs']} chunks={result4.upserted}")
    print(f"{'scheme':28s} {'chunks':>7s}")
    for scheme_id in sorted(outcome["per_scheme"]):
        print(f"{scheme_id:28s} {outcome['per_scheme'][scheme_id]:7d}")
    print(f"vectors={result3.vectors.shape[0]}x{result3.dim} cache_hit={result3.cache_hit}")
    print(f"collection={result4.collection} metric={result4.metric} count={result4.total_in_collection}")
    print(f"audit_passed={outcome['audit']['passed']} failures={outcome['audit']['failures']}")
    print(f"duration_s={round(outcome['duration_s'],2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
