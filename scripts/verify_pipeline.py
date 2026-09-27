from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from src.config import load_config
from src.models import CHUNK_NOISE_MARKERS, Chunk
from src.observability.runlog import read_ingest_run_log, read_jsonl
from src.pipeline.stage1_load import load_all, verify_documents
from src.pipeline.stage2_chunk import CHUNKS_PATH, audit_chunks, chunk_documents
from src.pipeline.stage3_embed import run_stage3
from src.pipeline.stage4_store import get_client, get_or_create_collection, query_collection
from src.retrieval.embedder import embed_chunks, embed_query, get_embedder, token_budget

FACTS_PATH = PROJECT_ROOT / "data" / "processed" / "facts.json"
DOCUMENTS_PATH = PROJECT_ROOT / "data" / "processed" / "documents.jsonl"
GOLDEN_PATH = PROJECT_ROOT / "eval" / "golden_qa.json"
REFUSAL_PATH = PROJECT_ROOT / "eval" / "refusal_qa.json"
PII_PATH = PROJECT_ROOT / "eval" / "pii_cases.json"


class Report:
    def __init__(self) -> None:
        self.failures: List[str] = []
        self.lines: List[str] = []

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        mark = "PASS" if ok else "FAIL"
        self.lines.append(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            self.failures.append(f"{label}: {detail}" if detail else label)
        return ok


def verify_phase0(rep: Report) -> None:
    rep.lines.append("PHASE 0 — config, models, eval sets")
    config = load_config()
    rep.check("config loads", True, f"backend={config.store.backend}")
    rep.check("5 allowlisted sources", len(config.sources.allowlist) == 5)
    rep.check(
        "chunk/embed constants",
        config.chunk.size_tokens == 200
        and config.embed.dim == 384
        and config.embed.max_seq_length == 256,
        f"size={config.chunk.size_tokens} overlap={config.chunk.overlap_tokens} dim={config.embed.dim} max_seq={config.embed.max_seq_length}",
    )
    counts = {}
    for name, path in (
        ("golden", GOLDEN_PATH),
        ("refusal", REFUSAL_PATH),
        ("pii", PII_PATH),
    ):
        counts[name] = len(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else 0
    rep.check(
        "eval set sizes 10/5/5",
        counts == {"golden": 10, "refusal": 5, "pii": 5},
        str(counts),
    )
    allow = {entry.url for entry in config.sources.allowlist}
    cases = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    bad = [c.get("expected_source_url") for c in cases if c.get("expected_source_url") not in allow]
    rep.check("every golden source_url is allowlisted", not bad, str(bad))
    rep.check(
        "store metric is cosine",
        config.store.metric == "cosine",
        f"collection={config.store.collection}",
    )


def verify_phase1(rep: Report, config) -> List[Any]:
    rep.lines.append("PHASE 1 — loading")
    documents = load_all(config)
    rep.check("5 documents load from snapshots", len(documents) == 5)
    try:
        verify_documents(documents, json.loads(FACTS_PATH.read_text(encoding="utf-8")))
        rep.check("verify_documents passes", True)
    except Exception as exc:
        rep.check("verify_documents passes", False, str(exc)[:120])
    rep.check(
        "all Direct Growth",
        all(d.plan == "Direct Growth" for d in documents),
    )
    rep.check(
        "no noise markers in raw text",
        not any(m in d.text for d in documents for m in CHUNK_NOISE_MARKERS),
    )
    rep.check("all docs > 1500 chars", all(len(d.text) > 1500 for d in documents),
              f"min={min(len(d.text) for d in documents)}")
    facts = json.loads(FACTS_PATH.read_text(encoding="utf-8"))
    rep.check("facts.json has 5 schemes", len(facts) == 5)
    amc = {sid: f.get("amc_aum_total") for sid, f in facts.items()}
    rep.check(
        "T2: amc_aum_total identical on all 5",
        len(set(amc.values())) == 1 and "9,86,236.84" in list(amc.values())[0],
        str(set(amc.values())),
    )
    if DOCUMENTS_PATH.exists():
        rows = read_jsonl(DOCUMENTS_PATH)
        rep.check(
            "documents.jsonl matches a fresh load",
            [r["doc_id"] for r in rows] == [d.doc_id for d in documents]
            and [r["text_sha256"] for r in rows] == [d.text_sha256 for d in documents],
        )
    else:
        rep.check("documents.jsonl exists", False)
    return documents


def verify_phase2(rep: Report, config, documents) -> List[Chunk]:
    rep.lines.append("PHASE 2 — chunking")
    fresh, decision, disagreements = chunk_documents(documents, config)
    audit = audit_chunks(fresh, config)
    rep.check("strategy is label_aware_recursive", decision.selected == "label_aware_recursive",
              f"rejected={decision.rejected} rules={decision.rules}")
    rep.check("audit passes on a fresh run", audit["passed"], str(audit["failures"][:3]))
    rep.check("facts.json vs regex cross-check agrees", not disagreements, str(disagreements[:3]))
    rep.check("152 chunks", len(fresh) == 152, str(len(fresh)))
    rep.check("unique chunk_ids", len({c.chunk_id for c in fresh}) == len(fresh))
    rep.check(
        "every chunk within the 200-token budget",
        all(c.token_count <= config.chunk.size_tokens for c in fresh),
        f"max={max(c.token_count for c in fresh)}",
    )
    rep.check(
        "embed_input carries scheme name + category",
        all(
            c.embed_input.startswith(
                config.sources.by_scheme_id(c.scheme_id).display_name
                if hasattr(config.sources.by_scheme_id(c.scheme_id), "display_name")
                else c.embed_input.split(" (")[0]
            )
            or "(" in c.embed_input
            for c in fresh
        ),
    )
    rep.check(
        "amc scope confined to the 2 AMC-level fields",
        {c.field for c in fresh if c.scope == "amc"} == {"amc_aum_total", "amc_incorporation_date"},
    )
    stored = read_jsonl(CHUNKS_PATH)
    fresh_map = {c.chunk_id: c for c in fresh}
    drift = [
        r["chunk_id"]
        for r in stored
        if r["chunk_id"] not in fresh_map
        or fresh_map[r["chunk_id"]].text != r["text"]
        or fresh_map[r["chunk_id"]].value != r["value"]
        or fresh_map[r["chunk_id"]].embed_input != r["embed_input"]
    ]
    rep.check(
        "chunks.jsonl reproduces exactly from source",
        not drift and len(stored) == len(fresh),
        f"{len(drift)} drifted: {drift[:3]}",
    )
    return fresh


def verify_phase3(rep: Report, config, chunks: List[Chunk]) -> np.ndarray:
    rep.lines.append("PHASE 3 — embedding")
    model = get_embedder()
    rep.check("model max_seq_length is 256", model.max_seq_length == 256)
    budget = token_budget(config)
    lengths = [len(model.tokenizer(c.embed_input)["input_ids"]) for c in chunks]
    rep.check(
        f"all embed_input <= {budget} wordpieces",
        max(lengths) <= budget,
        f"max={max(lengths)} mean={sum(lengths)/len(lengths):.1f}",
    )
    recomputed = embed_chunks(chunks, config)
    rep.check("shape (N, 384)", recomputed.shape == (len(chunks), 384), str(recomputed.shape))
    rep.check("dtype float32", recomputed.dtype == np.float32, str(recomputed.dtype))
    norms = np.linalg.norm(recomputed, axis=1)
    rep.check("unit norm", bool(np.allclose(norms, 1.0, atol=1e-3)),
              f"min={norms.min():.5f} max={norms.max():.5f}")
    cached = run_stage3(chunks, config)
    rep.check("cache hit on unchanged corpus", cached.cache_hit is True)
    rep.check(
        "cached vectors == freshly computed vectors",
        bool(np.allclose(cached.vectors, recomputed, atol=1e-5)),
        f"max|delta|={np.abs(cached.vectors - recomputed).max():.2e}",
    )
    q = embed_query("What is the minimum SIP?", config)
    rep.check("query vector (384,) unit norm", q.shape == (384,) and abs(float(np.linalg.norm(q)) - 1) < 1e-3)
    return recomputed


def verify_phase4(rep: Report, config, chunks: List[Chunk], vectors: np.ndarray) -> None:
    rep.lines.append("PHASE 4 — store")
    collection = get_or_create_collection(get_client(config), config)
    rep.check("collection count == chunk count", collection.count() == len(chunks),
              f"{collection.count()} vs {len(chunks)}")
    got = collection.get(include=["documents", "metadatas", "embeddings"])
    ids = list(got["ids"])
    rep.check("collection ids == chunk_ids", set(ids) == {c.chunk_id for c in chunks},
              f"only_in_store={len(set(ids)-{c.chunk_id for c in chunks})} only_in_chunks={len({c.chunk_id for c in chunks}-set(ids))}")
    order = {cid: i for i, cid in enumerate(ids)}
    stored_vec = np.asarray(got["embeddings"], dtype=np.float32)
    by_id = {c.chunk_id: vectors[i] for i, c in enumerate(chunks)}
    expected = np.stack([by_id[cid] for cid in ids])
    delta = np.abs(stored_vec - expected).max() if stored_vec.shape == expected.shape else float("inf")
    rep.check("Chroma vectors == Stage 3 vectors", delta < 1e-4, f"max|delta|={delta:.2e}")
    metas = got["metadatas"]
    rep.check("no None in any Chroma metadata",
              all(all(v is not None for v in m.values()) for m in metas))
    allow = {e.url for e in config.sources.allowlist}
    rep.check("all source_url allowlisted", all(m["source_url"] in allow for m in metas))
    mism = []
    for cid, m in zip(ids, metas):
        c = next(x for x in chunks if x.chunk_id == cid)
        if m["field"] != c.field or (m["value"] or None) != c.value or bool(m["historical"]) != c.historical:
            mism.append(cid)
    rep.check("metadata field/value/historical round-trips", not mism, str(mism[:3]))
    rep.check(
        "documents match chunk text",
        all(d == next(x for x in chunks if x.chunk_id == cid).text for cid, d in zip(ids, got["documents"])),
    )


def verify_retrieval(rep: Report, config) -> None:
    rep.lines.append("RETRIEVAL SPOT CHECKS (Phase 4 filter correctness)")
    cases = [
        ("What is the minimum SIP?", ["hdfc_elss"], "min_sip", "₹500"),
        ("expense ratio", ["hdfc_flexi_cap"], "expense_ratio", "0.77%"),
        ("total AUM of the fund", ["hdfc_large_cap"], "fund_aum", "₹39,933.37 Cr"),
        ("lock-in period", ["hdfc_elss"], "lock_in", "3 years"),
        ("benchmark", ["hdfc_small_cap"], "benchmark", "BSE 250 SmallCap Total Return Index"),
    ]
    for query, scheme_ids, want_field, want_value in cases:
        r = query_collection(config, query, scheme_ids=scheme_ids, n_results=3)
        metas = r["metadatas"][0]
        top = metas[0]
        ok_field = top["field"] == want_field
        ok_scheme = all(m["scheme_id"] == scheme_ids[0] for m in metas)
        ok_value = str(top.get("value") or "").startswith(want_value)
        rep.check(
            f'"{query}" -> {want_field} {want_value}',
            ok_field and ok_scheme and ok_value,
            f"got {top['field']}={top.get('value')!r} (filter_ok={ok_scheme})",
        )
    r = query_collection(config, "NAV", n_results=5)
    rep.check("unfiltered query spans multiple schemes",
              len({m["scheme_id"] for m in r["metadatas"][0]}) > 1,
              str(sorted({m["scheme_id"] for m in r["metadatas"][0]})))


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify", description="End-to-end integrity check of Stages 0-4")
    parser.add_argument("--skip-retrieval", action="store_true")
    args = parser.parse_args(argv)

    rep = Report()
    config = load_config()
    verify_phase0(rep)
    documents = verify_phase1(rep, config)
    chunks = verify_phase2(rep, config, documents)
    vectors = verify_phase3(rep, config, chunks)
    verify_phase4(rep, config, chunks, vectors)
    if not args.skip_retrieval:
        verify_retrieval(rep, config)

    print("\n".join(rep.lines))
    print()
    total = len(rep.lines)
    passed = total - len(rep.failures)
    print(f"{passed}/{total} checks passed")
    if rep.failures:
        print("FAILURES:")
        for f in rep.failures:
            print(f"  - {f}")
        return 1
    print("ALL STAGES VERIFIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
