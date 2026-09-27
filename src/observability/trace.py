"""Trace builder for the UI's "How this was answered" panel (architecture 10).

One sub-object per stage, filled in as the answer is produced. This is what
turns "a chatbot" into a demonstrable RAG system: the reviewer can see exactly
which page was fetched, which chunks were selected, and which checks the final
answer passed.

The API key is never a field here, and a PII guard decision carries
``loggable=False`` so the query text behind it is not written out.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence

from src.models import (
    Answer,
    GuardDecision,
    RetrievedChunk,
    SchemeResolution,
    Stage1Trace,
    Stage2Trace,
    Stage3Trace,
    Stage4Trace,
    Stage5Trace,
    Stage6Trace,
    Trace,
    ValidationResult,
)


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def ingest_trace() -> Dict[str, Any]:
    """Stages 1-4 as recorded by the last offline ingest run.

    Stages 1-3 cannot be recomputed per question - the fetch, the chunking and
    the embedding happened once, offline. The run log is the record of what
    actually happened, so the UI shows that rather than pretending to re-derive
    it. Any stage missing from the log is returned as ``None``.
    """
    from src.observability.runlog import read_ingest_run_log

    try:
        log = read_ingest_run_log()
    except Exception:
        return {}
    trace = log.get("trace") or {}
    return {f"stage{index}": trace.get(f"stage{index}") for index in range(1, 5)}


def build_trace(
    stage1: Optional[Stage1Trace] = None,
    stage2: Optional[Stage2Trace] = None,
    stage3: Optional[Stage3Trace] = None,
    stage4: Optional[Stage4Trace] = None,
    stage5: Optional[Stage5Trace] = None,
    stage6: Optional[Stage6Trace] = None,
) -> Trace:
    return Trace(
        stage1=stage1, stage2=stage2, stage3=stage3,
        stage4=stage4, stage5=stage5, stage6=stage6,
    )


def build_full_trace(
    stage4: Optional[Stage4Trace] = None,
    stage5: Optional[Stage5Trace] = None,
    stage6: Optional[Stage6Trace] = None,
) -> Trace:
    """Stages 1-3 from the ingest log, stage 4 live, 5-6 for this question.

    Stage 4 is read from the running collection rather than the log, so the
    count shown is the count actually being searched.
    """
    recorded = ingest_trace()

    def _coerce(model, payload):
        if not isinstance(payload, dict):
            return None
        try:
            return model(**payload)
        except Exception:
            return None

    return Trace(
        stage1=_coerce(Stage1Trace, recorded.get("stage1")),
        stage2=_coerce(Stage2Trace, recorded.get("stage2")),
        stage3=_coerce(Stage3Trace, recorded.get("stage3")),
        stage4=stage4 or _coerce(Stage4Trace, recorded.get("stage4")),
        stage5=stage5,
        stage6=stage6,
    )


def stage5_trace(
    guard_decisions: Sequence[GuardDecision],
    scheme_resolution: Optional[SchemeResolution],
    selected: Sequence[RetrievedChunk],
) -> Stage5Trace:
    return Stage5Trace(
        guard_decisions=list(guard_decisions),
        scheme_resolution=scheme_resolution,
        top_chunks=[
            {
                "rank": item.rank,
                "chunk_id": item.chunk.chunk_id,
                "scheme_id": item.chunk.scheme_id,
                "field": item.chunk.field,
                "value": item.chunk.value,
                "cosine_distance": round(item.cosine_distance, 6),
                "source_url": item.chunk.source_url,
            }
            for item in selected
        ],
    )


def stage6_trace(
    citation_url: Optional[str],
    validation: Optional[ValidationResult],
    retry_count: int,
) -> Stage6Trace:
    return Stage6Trace(
        citation_url=citation_url, validation=validation, retry_count=retry_count
    )


def trace_to_json(trace: Optional[Trace], indent: int = 2) -> str:
    """Serialise for the UI expander. Never raises on a partial trace."""
    if trace is None:
        return "{}"
    try:
        return trace.model_dump_json(indent=indent)
    except Exception:
        return json.dumps({"error": "trace could not be serialised"}, indent=indent)


__all__ = [
    "Trace",
    "build_trace",
    "build_full_trace",
    "ingest_trace",
    "stage5_trace",
    "stage6_trace",
    "trace_to_json",
    "sha256_text",
]
