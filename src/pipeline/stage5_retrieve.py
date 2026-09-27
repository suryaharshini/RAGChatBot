from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional, Sequence

from src.config import AppConfig, load_config
from src.models import (
    Chunk,
    GuardDecision,
    RetrievalOutcome,
    RetrievedChunk,
    SchemeResolution,
)

REFUSAL_UNGROUNDED = (
    "I could not find that in the 5 HDFC Mutual Fund schemes I cover. Please check "
    "the source page directly."
)


def _normalise(query: str, config: AppConfig) -> str:
    text = re.sub(r"\s+", " ", query or "").strip()
    return text[: config.guards.max_query_chars]


def _refusal(
    refusal_type: str, text: str, decisions: List[GuardDecision]
) -> RetrievalOutcome:
    return RetrievalOutcome(
        query="",
        refused=True,
        refusal_type=refusal_type,
        refusal_text=text,
        guard_decisions=decisions,
        grounded=False,
        prompt_context="",
    )


def retrieve(
    query: str,
    config: Optional[AppConfig] = None,
    embedder: Optional[Any] = None,
    llm: Optional[Any] = None,
    scheme_hint: Optional[Sequence[str]] = None,
) -> RetrievalOutcome:
    """Run the guard order, resolve the scheme, and retrieve.

    ``scheme_hint`` supplies scheme_ids to use **only** when the query itself
    resolves to nothing, so a follow-up like "and the lock-in period?" can
    inherit the scheme from the conversation. It never overrides a scheme the
    query names explicitly, and it never affects the guards, which have already
    run on the query text by that point.
    """
    config = config or load_config()
    decisions: List[GuardDecision] = []
    outcome = RetrievalOutcome(query="")

    text = _normalise(query, config)

    from src.guards.pii import detect_pii

    hit = detect_pii(text)
    decisions.append(
        GuardDecision(
            guard="pii",
            triggered=hit is not None,
            reason=f"detected {hit.kind}" if hit else "no personal identifier found",
            refusal_text=None,
            loggable=False,
        )
    )
    if hit is not None:
        from src.guards.pii import refusal_for_pii

        outcome.refused = True
        outcome.refusal_type = "pii"
        outcome.refusal_text = refusal_for_pii()
        outcome.guard_decisions = decisions
        outcome.query = ""
        return outcome

    from src.guards.intent import (
        build_out_of_corpus_refusal,
        detect_advice_two_layer,
        detect_out_of_corpus,
        refusal_for_advice,
    )
    from src.guards.performance import detect_performance_two_layer, refusal_for_performance

    advice_hit, advice_reason = detect_advice_two_layer(text, llm)
    decisions.append(
        GuardDecision(guard="advice", triggered=advice_hit, reason=advice_reason)
    )
    if advice_hit:
        outcome.refused = True
        outcome.refusal_type = "advice"
        outcome.refusal_text = refusal_for_advice()
        outcome.guard_decisions = decisions
        outcome.query = text
        return outcome

    perf_hit, perf_reason = detect_performance_two_layer(text, llm)
    decisions.append(
        GuardDecision(guard="performance", triggered=perf_hit, reason=perf_reason)
    )
    if perf_hit:
        outcome.refused = True
        outcome.refusal_type = "performance"
        outcome.refusal_text = refusal_for_performance()
        outcome.guard_decisions = decisions
        outcome.query = text
        return outcome

    from src.retrieval.aliases import resolve_scheme

    resolution = resolve_scheme(text, config)
    ooc_hit, ooc_reason = detect_out_of_corpus(text, config)
    if not resolution.scheme_ids and scheme_hint:
        # Follow-up with no scheme of its own: inherit from the conversation.
        resolution = SchemeResolution(
            scheme_ids=list(scheme_hint),
            matched_alias="from conversation history",
            confidence=0.5,
            ambiguous=False,
        )
    decisions.append(
        GuardDecision(
            guard="out_of_corpus",
            triggered=ooc_hit and not resolution.scheme_ids,
            reason=ooc_reason or "resolved in scope",
        )
    )
    if ooc_hit and not resolution.scheme_ids:
        outcome.refused = True
        outcome.refusal_type = "out_of_corpus"
        outcome.refusal_text = build_out_of_corpus_refusal(config)
        outcome.guard_decisions = decisions
        outcome.scheme_resolution = resolution
        outcome.query = text
        return outcome

    from src.pipeline.stage4_store import query_collection
    from src.retrieval.rerank import mmr_rerank, similarity_floor_check

    candidate_k = config.retrieve.candidate_k

    # A follow-up that inherits its scheme from the conversation ("and the
    # lock-in period?") carries almost no lexical signal, so its cosine against
    # a prefixed chunk falls under the sim_floor and the correct chunk is
    # discarded as "ungrounded". Expanding the *retrieval* text with the scheme
    # name the hint already established restores the match. The guards above
    # have already run on the original query, and sim_floor is left untouched.
    retrieval_text = text
    if scheme_hint and resolution.matched_alias == "from conversation history":
        names = [
            source.scheme_name
            for source in config.sources.allowlist
            if source.scheme_id in resolution.scheme_ids
        ]
        if names:
            retrieval_text = f"{names[0]} {text}"

    result = query_collection(
        config, retrieval_text, scheme_ids=resolution.scheme_ids or None, n_results=candidate_k
    )
    candidates: List[RetrievedChunk] = []
    for rank, (chunk_id, meta, distance) in enumerate(
        zip(result["ids"][0], result["metadatas"][0], result["distances"][0])
    ):
        chunk = _chunk_from_metadata(chunk_id, meta, result["documents"][0][rank], config)
        candidates.append(
            RetrievedChunk(chunk=chunk, cosine_distance=float(distance), rank=rank)
        )

    from src.retrieval.embedder import embed_query

    query_vec = embed_query(text, config)
    selected = mmr_rerank(
        query_vec, candidates, k=4, lambda_=config.retrieve.mmr_lambda
    )
    grounded, best_similarity = similarity_floor_check(candidates, config.retrieve.sim_floor)
    decisions.append(
        GuardDecision(
            guard="sim_floor",
            triggered=not grounded,
            reason=(
                f"best cosine similarity {best_similarity:.4f} "
                f"{'below' if not grounded else 'at or above'} floor "
                f"{config.retrieve.sim_floor}"
            ),
        )
    )

    outcome.query = text
    outcome.guard_decisions = decisions
    outcome.scheme_resolution = resolution
    outcome.candidates = candidates
    outcome.grounded = grounded
    if not grounded:
        outcome.selected = []
        outcome.prompt_context = ""
        outcome.refusal_type = "ungrounded"
        outcome.refused = True
        outcome.refusal_text = REFUSAL_UNGROUNDED
        return outcome
    outcome.selected = selected
    outcome.prompt_context = build_prompt_context(selected, config)
    return outcome


def _chunk_from_metadata(
    chunk_id: str, meta: Dict[str, Any], text: str, config: AppConfig
) -> Chunk:
    from src.pipeline.stage2_chunk import CHUNKS_PATH
    from src.observability.runlog import read_jsonl

    global _CHUNK_CACHE
    try:
        cache = _CHUNK_CACHE
    except NameError:
        cache = _CHUNK_CACHE = {}
    if not cache:
        for row in read_jsonl(CHUNKS_PATH):
            cache[row["chunk_id"]] = Chunk(**row)
    if chunk_id in cache:
        return cache[chunk_id]
    return Chunk(
        chunk_id=chunk_id,
        doc_id=meta.get("doc_id", meta.get("scheme_id", "")),
        scheme_id=meta["scheme_id"],
        section=meta.get("section", ""),
        field=meta.get("field", ""),
        text=text,
        embed_input=text,
        source_url=meta.get("source_url", ""),
        value=meta.get("value") or None,
        value_type=meta.get("value_type", "text"),
        scope=meta.get("scope", "fund"),
        is_fact=bool(meta.get("is_fact", False)),
        historical=bool(meta.get("historical", False)),
    )


def build_prompt_context(
    selected: List[RetrievedChunk], config: AppConfig
) -> str:
    names = {entry.scheme_id: entry.scheme_name for entry in config.sources.allowlist}
    blocks: List[str] = []
    for item in selected:
        chunk = item.chunk
        name = names.get(chunk.scheme_id, chunk.scheme_id)
        qualifier = " [SUPERSEDED - not current]" if chunk.historical else ""
        blocks.append(
            f"[{chunk.field}]{qualifier} {name} (Direct Growth): {chunk.value or chunk.text}\n"
            f"source: {chunk.source_url}"
        )
    return "\n\n".join(blocks)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="stage5_retrieve")
    parser.add_argument("query", nargs="*")
    args = parser.parse_args(argv)
    config = load_config()
    queries = args.query or ["What is the minimum SIP?"]
    for q in queries:
        outcome = retrieve(q, config)
        print(f"\nQ: {q}")
        print(f"  refused={outcome.refused} type={outcome.refusal_type} grounded={outcome.grounded}")
        for decision in outcome.guard_decisions:
            flag = "TRIP" if decision.triggered else "pass"
            print(f"    [{flag}] {decision.guard}: {decision.reason}")
        if outcome.scheme_resolution is not None:
            print(f"  scheme_ids={outcome.scheme_resolution.scheme_ids} ambiguous={outcome.scheme_resolution.ambiguous}")
        for item in outcome.selected:
            print(f"    #{item.rank} {item.chunk.field}={item.chunk.value!r} d={item.cosine_distance:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
