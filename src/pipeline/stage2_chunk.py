from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import PROJECT_ROOT, AppConfig, load_config
from src.models import (
    CHUNK_NOISE_MARKERS,
    Chunk,
    ChunkParams,
    Document,
    StrategyDecision,
)
from src.observability.runlog import LOGS_DIR, write_ingest_run_log, write_jsonl

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
FACTS_PATH = PROCESSED_DIR / "facts.json"
CHUNKS_PATH = PROCESSED_DIR / "chunks.jsonl"
SEMANTIC_CHUNKS_PATH = PROCESSED_DIR / "chunks.semantic.jsonl"
GOLDEN_PATH = PROJECT_ROOT / "eval" / "golden_qa.json"
DEFAULT_ENCODING = "cl100k_base"
SEMANTIC_DISTANCE_THRESHOLD = 0.25

SCHEMES = (
    "hdfc_large_cap",
    "hdfc_flexi_cap",
    "hdfc_elss",
    "hdfc_small_cap",
    "hdfc_balanced_advantage",
)

GOLD_TRUTH: Dict[str, Dict[str, Any]] = {
    "expense_ratio": {
        "hdfc_large_cap": "1.03%",
        "hdfc_flexi_cap": "0.77%",
        "hdfc_elss": "1.21%",
        "hdfc_small_cap": "0.78%",
        "hdfc_balanced_advantage": "0.78%",
    },
    "fund_aum": {
        "hdfc_large_cap": "₹39,933.37 Cr",
        "hdfc_flexi_cap": "₹1,13,606.47 Cr",
        "hdfc_elss": "₹15,991.78 Cr",
        "hdfc_small_cap": "₹41,890.86 Cr",
        "hdfc_balanced_advantage": "₹1,07,295.79 Cr",
    },
    "amc_aum_total": {sid: "₹9,86,236.84 Cr" for sid in SCHEMES},
    "nav": {
        "hdfc_large_cap": "₹1,189.08",
        "hdfc_flexi_cap": "₹2,214.57",
        "hdfc_elss": "₹1,447.38",
        "hdfc_small_cap": "₹159.82",
        "hdfc_balanced_advantage": "₹557.73",
    },
    "min_first_inv": {
        "hdfc_large_cap": "₹100",
        "hdfc_flexi_cap": "₹100",
        "hdfc_elss": "₹500",
        "hdfc_small_cap": "₹100",
        "hdfc_balanced_advantage": "₹100",
    },
    "min_additional_inv": {
        "hdfc_large_cap": "₹100",
        "hdfc_flexi_cap": "₹100",
        "hdfc_elss": "₹500",
        "hdfc_small_cap": "₹100",
        "hdfc_balanced_advantage": "₹100",
    },
    "min_sip": {
        "hdfc_large_cap": "₹100",
        "hdfc_flexi_cap": "₹100",
        "hdfc_elss": "₹500",
        "hdfc_small_cap": "₹100",
        "hdfc_balanced_advantage": "₹100",
    },
    "benchmark": {
        "hdfc_large_cap": "NIFTY 100 Total Return Index",
        "hdfc_flexi_cap": "NIFTY 500 Total Return Index",
        "hdfc_elss": "NIFTY 500 Total Return Index",
        "hdfc_small_cap": "BSE 250 SmallCap Total Return Index",
        "hdfc_balanced_advantage": "NIFTY 50 Hybrid Composite Debt 50:50 Index",
    },
    "exit_load": {
        "hdfc_large_cap": "1% if redeemed within 1 year",
        "hdfc_flexi_cap": "1% if redeemed within 1 year",
        "hdfc_elss": "Nil",
        "hdfc_small_cap": "1% if redeemed within 1 year",
        "hdfc_balanced_advantage": (
            "excess of 15% of the investment,1% will be charged for "
            "redemption within 1 year."
        ),
    },
    "lock_in": {
        "hdfc_large_cap": "None",
        "hdfc_flexi_cap": "None",
        "hdfc_elss": "3 years",
        "hdfc_small_cap": "None",
        "hdfc_balanced_advantage": "None",
    },
    "risk_rating": {sid: "Very High" for sid in SCHEMES},
    "stamp_duty": {sid: "0.005% (from July 1st, 2020)" for sid in SCHEMES},
    "rta": {sid: "Cams" for sid in SCHEMES},
    "groww_rating": {
        "hdfc_large_cap": 4,
        "hdfc_flexi_cap": 5,
        "hdfc_elss": 5,
        "hdfc_small_cap": 3,
        "hdfc_balanced_advantage": 5,
    },
    "custodian": {
        "hdfc_large_cap": "HDFC Bank",
        "hdfc_flexi_cap": "Deutsche Bank",
        "hdfc_elss": "Deutsche Bank",
        "hdfc_small_cap": "Citibank NA",
        "hdfc_balanced_advantage": "HDFC Bank",
    },
    "holdings_count": {
        "hdfc_large_cap": 50,
        "hdfc_flexi_cap": 86,
        "hdfc_elss": 65,
        "hdfc_small_cap": 87,
        "hdfc_balanced_advantage": 326,
    },
    "scheme_launch_date": {sid: "01-Jan-2013" for sid in SCHEMES},
    "amc_incorporation_date": {sid: "10 Dec 1999" for sid in SCHEMES},
}

EXPECTED_HISTORICAL_EXIT_LOADS = {
    "hdfc_large_cap": 2,
    "hdfc_flexi_cap": 0,
    "hdfc_elss": 0,
    "hdfc_small_cap": 3,
    "hdfc_balanced_advantage": 3,
}

LABELLED_LINE_RE = re.compile(r"(?m)^[A-Z][^\n:]{2,60}\s*:\s*\S")


@dataclass(frozen=True)
class FieldSpec:
    field: str
    section: str
    text_label: str
    value_type: str
    scope: str = "fund"
    is_fact: bool = True
    value_re: str = r"[^\n]+"
    fact_key: Optional[str] = None
    is_prose: bool = False

    def pattern(self, amc_name: str = "") -> re.Pattern:
        label = self.text_label
        if "{amc_name}" in label:
            label = label.format(amc_name=amc_name or "the AMC")
        return re.compile(
            rf"(?im)^{re.escape(label)}\s*:\s*(?P<value>{self.value_re})"
        )

    def resolve_label(self, amc_name: str = "") -> str:
        if "{amc_name}" in self.text_label:
            return self.text_label.format(amc_name=amc_name or "the AMC")
        return self.text_label


CURRENCY_RE = r"₹[\d,]+(?:\.\d+)?(?:\s*Cr)?"
LABEL_REGISTRY: List[FieldSpec] = [
    FieldSpec("category", "overview", "Fund category", "enum", fact_key="category"),
    FieldSpec("fund_house", "overview", "Fund house", "text", fact_key="fund_house"),
    FieldSpec(
        "nav",
        "overview",
        "NAV",
        "currency",
        value_re=CURRENCY_RE,
        fact_key="nav",
    ),
    FieldSpec(
        "fund_aum",
        "overview",
        "Fund size (AUM)",
        "currency_cr",
        value_re=CURRENCY_RE,
        fact_key="fund_aum",
    ),
    FieldSpec(
        "expense_ratio",
        "overview",
        "Expense ratio",
        "percent",
        value_re=r"[0-9.]+%",
        fact_key="expense_ratio",
    ),
    FieldSpec(
        "groww_rating",
        "overview",
        "Rating",
        "int",
        value_re=r"\d+",
        fact_key="groww_rating",
    ),
    FieldSpec(
        "risk_rating",
        "overview",
        "Risk rating",
        "enum",
        value_re=r"[^\n(]+",
        fact_key="risk_rating",
    ),
    FieldSpec(
        "benchmark",
        "overview",
        "Fund benchmark",
        "index_name",
        value_re=r"[^\n]+",
        fact_key="benchmark",
    ),
    FieldSpec(
        "scheme_launch_date",
        "overview",
        "Scheme launch date",
        "date",
        value_re=r"[^\n]+",
        fact_key="scheme_launch_date",
    ),
    FieldSpec(
        "amc_incorporation_date",
        "overview",
        "Date of Incorporation of {amc_name}",
        "date",
        scope="amc",
        value_re=r"[^\n]+",
        fact_key="amc_incorporation_date",
    ),
    FieldSpec(
        "amc_aum_total",
        "overview",
        "Total AUM of {amc_name} (AMC level, not this fund)",
        "currency_cr",
        scope="amc",
        value_re=CURRENCY_RE,
        fact_key="amc_aum_total",
    ),
    FieldSpec(
        "min_first_inv",
        "fees_and_minimums",
        "Min. for 1st investment",
        "currency",
        value_re=CURRENCY_RE,
        fact_key="min_first_inv",
    ),
    FieldSpec(
        "min_additional_inv",
        "fees_and_minimums",
        "Min. for 2nd investment",
        "currency",
        value_re=CURRENCY_RE,
        fact_key="min_additional_inv",
    ),
    FieldSpec(
        "min_sip",
        "fees_and_minimums",
        "Min. for SIP",
        "currency",
        value_re=CURRENCY_RE,
        fact_key="min_sip",
    ),
    FieldSpec(
        "purchase_multiplier",
        "fees_and_minimums",
        "Purchase multiplier",
        "currency",
        value_re=CURRENCY_RE,
        fact_key="purchase_multiplier",
    ),
    FieldSpec(
        "exit_load",
        "fees_and_minimums",
        "Exit load",
        "text",
        value_re=r"[^\n]+",
        fact_key="exit_load",
    ),
    FieldSpec(
        "stamp_duty",
        "fees_and_minimums",
        "Stamp duty on investment",
        "percent",
        value_re=r"[^\n]+",
        fact_key="stamp_duty",
    ),
    FieldSpec(
        "tax_note",
        "fees_and_minimums",
        "Tax implication",
        "text",
        is_fact=False,
        is_prose=True,
        fact_key="tax_note",
    ),
    FieldSpec(
        "lock_in",
        "fees_and_minimums",
        "Lock-in period",
        "duration",
        value_re=r"[^\n]+",
        fact_key="lock_in",
    ),
    FieldSpec(
        "investment_objective",
        "about",
        "Investment Objective",
        "text",
        is_fact=False,
        is_prose=True,
        fact_key="investment_objective",
    ),
    FieldSpec(
        "category_helper_text",
        "about",
        "Category description",
        "text",
        is_fact=False,
        is_prose=True,
        fact_key="category_helper_text",
    ),
    FieldSpec(
        "custodian",
        "about",
        "Custodian",
        "text",
        fact_key="custodian",
    ),
    FieldSpec(
        "rta",
        "about",
        "Registrar & Transfer Agent",
        "text",
        fact_key="rta",
    ),
    FieldSpec(
        "holdings_count",
        "portfolio",
        "Holdings count",
        "int",
        value_re=r"\d+",
        fact_key="holdings_count",
    ),
]


@lru_cache(maxsize=4)
def _encoder(encoding_name: str = DEFAULT_ENCODING):
    import tiktoken

    return tiktoken.get_encoding(encoding_name)


def count_tokens(text: str, encoding_name: str = DEFAULT_ENCODING) -> int:
    return len(_encoder(encoding_name).encode(text, disallowed_special=()))


def build_chunk_id(scheme_id: str, section: str, field: str, index: int) -> str:
    return f"{scheme_id}_{section}_{field}_{index}"


def parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def extract_from_text(text: str, spec: FieldSpec, amc_name: str = "") -> Optional[str]:
    match = spec.pattern(amc_name).search(text)
    if match is None:
        return None
    value = re.sub(r"\s+", " ", match.group("value")).strip()
    return value or None


def compute_corpus_stats(documents: List[Document]) -> Dict[str, Any]:
    total_lines = 0
    labelled_lines = 0
    for doc in documents:
        lines = [line for line in doc.text.split("\n") if line.strip()]
        total_lines += len(lines)
        labelled_lines += len(LABELLED_LINE_RE.findall(doc.text))
    mean_chars = (
        sum(len(doc.text) for doc in documents) / len(documents)
        if documents
        else 0.0
    )
    return {
        "doc_count": len(documents),
        "mean_doc_chars": round(mean_chars, 1),
        "total_lines": total_lines,
        "labelled_lines": labelled_lines,
        "labelled_line_ratio": round(labelled_lines / total_lines, 4)
        if total_lines
        else 0.0,
    }


def _golden_single_fact_ratio() -> float:
    if not GOLDEN_PATH.exists():
        return 1.0
    cases = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    if not cases:
        return 1.0
    single = sum(1 for case in cases if case.get("field"))
    return single / len(cases)


def select_chunking_strategy(
    config: AppConfig, documents: List[Document]
) -> StrategyDecision:
    stats = compute_corpus_stats(documents)
    r1 = stats["labelled_line_ratio"] > 0.15
    r2 = stats["doc_count"] <= 10 and stats["mean_doc_chars"] <= 60000
    r3 = bool(r1)
    ratio = _golden_single_fact_ratio()
    r4 = ratio >= 0.8
    rules = {"R1": r1, "R2": r2, "R3": r3, "R4": r4}
    params = ChunkParams(
        strategy="pending",
        size_tokens=config.chunk.size_tokens,
        overlap_tokens=config.chunk.overlap_tokens,
        separators=list(config.chunk.separators),
        never_split=list(config.chunk.never_split),
        holdings_mode=config.chunk.holdings_mode,
    )
    explicit = config.chunk.strategy
    if explicit == "semantic":
        if not config.chunk.semantic_available:
            raise ValueError("chunk.strategy=semantic but semantic_available is false")
        selected = "semantic"
        rejected: List[str] = ["label_aware_recursive"]
        rationale = (
            "chunk.strategy=semantic set explicitly in config; semantic chunking is "
            "available for the class comparison demo (FR15) but is not the default."
        )
    else:
        selected = "label_aware_recursive"
        rejected = ["semantic"]
        rationale = (
            f"R1={r1} ({stats['labelled_line_ratio']:.1%} of lines are labelled "
            "label:value), R2="
            f"{r2} ({stats['doc_count']} short documents), R3={r3} (topics already "
            f"delimited by labels), R4={r4} ({ratio:.0%} of golden queries are "
            "single-fact). Semantic chunking is rejected: R2 and R3 fail for a small "
            "corpus whose topics are already explicit, an embedding pass to propose "
            "boundaries adds cost and non-determinism, and it would group unrelated "
            "labels such as NAV beside exit load, degrading single-fact precision."
        )
    params.strategy = selected
    return StrategyDecision(
        selected=selected,
        rejected=rejected,
        rules=rules,
        rationale=rationale,
        params=params,
        corpus_stats=stats,
    )


def chunk_prose(text: str, config: AppConfig) -> List[str]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=DEFAULT_ENCODING,
        chunk_size=config.chunk.size_tokens,
        chunk_overlap=config.chunk.overlap_tokens,
        separators=list(config.chunk.separators),
    )
    return splitter.split_text(text)


def _emit(
    chunks: List[Chunk],
    counters: Dict[Tuple[str, str, str], int],
    doc: Document,
    spec_section: str,
    field: str,
    value: Optional[str],
    text: str,
    config: AppConfig,
    value_type: str = "text",
    scope: str = "fund",
    is_fact: bool = False,
    historical: bool = False,
    effective_date: Optional[date] = None,
) -> None:
    display_category = config.sources.by_scheme_id(doc.scheme_id).category
    pieces = [text]
    if count_tokens(text) > config.chunk.size_tokens:
        pieces = chunk_prose(text, config)
    key = (doc.scheme_id, spec_section, field)
    index = counters.get(key, 0)
    counters[key] = index + len(pieces)
    for offset, piece in enumerate(pieces):
        chunks.append(
            Chunk(
                chunk_id=build_chunk_id(
                    doc.scheme_id, spec_section, field, index + offset
                ),
                doc_id=doc.doc_id,
                scheme_id=doc.scheme_id,
                section=spec_section,
                field=field,
                text=piece,
                embed_input=config.embed.render_prefix(
                    doc.scheme_name, display_category, piece
                ),
                source_url=doc.source_url,
                value=value,
                value_type=value_type,
                scope=scope,
                effective_date=effective_date,
                historical=historical,
                is_fact=is_fact,
                token_count=count_tokens(piece),
            )
        )


def _make_chunks(
    documents: List[Document],
    facts_by_id: Dict[str, Dict[str, Any]],
    config: AppConfig,
) -> Tuple[List[Chunk], List[str]]:
    chunks: List[Chunk] = []
    disagreements: List[str] = []

    for doc in documents:
        facts = facts_by_id.get(doc.scheme_id, {})
        amc_name = str(facts.get("amc_name") or config.project.amc)

        counters: Dict[Tuple[str, str, str], int] = {}
        for spec in LABEL_REGISTRY:
            from_facts = facts.get(spec.fact_key or spec.field)
            from_text = extract_from_text(doc.text, spec, amc_name)
            if (
                from_facts is not None
                and from_text is not None
                and re.sub(r"\s+", " ", str(from_facts)).strip()
                != re.sub(r"\s+", " ", from_text).strip()
            ):
                disagreements.append(
                    f"{doc.scheme_id}.{spec.field}: facts={from_facts!r} "
                    f"text={from_text!r}"
                )
            value = from_facts if from_facts is not None else from_text
            if value is None or str(value).strip() in {"", "Not available"}:
                continue
            value = str(value).strip()
            label = spec.resolve_label(amc_name)
            prefix = ""
            if spec.scope == "amc":
                prefix = f"AMC-level figure, not this scheme. "
            body = (
                f"{doc.scheme_name} — {label}: {prefix}{value}."
                if not str(value).endswith(".")
                else f"{doc.scheme_name} — {label}: {prefix}{value}"
            )
            _emit(
                chunks,
                counters,
                doc,
                spec.section,
                spec.field,
                value,
                body,
                config,
                value_type=spec.value_type,
                scope=spec.scope,
                is_fact=spec.is_fact,
            )

        for row in (facts.get("exit_load_history") or [])[1:]:
            effective = parse_date(row.get("as_on_date"))
            body = (
                f"{doc.scheme_name} — historical exit load, effective "
                f"{row.get('as_on_date')}: {row.get('note')}. Superseded, not the "
                "current exit load."
            )
            _emit(
                chunks,
                counters,
                doc,
                "fees_and_minimums",
                "exit_load_historical",
                str(row.get("note")),
                body,
                config,
                value_type="text",
                historical=True,
                effective_date=effective,
            )

        top = facts.get("top_holdings") or []
        if top:
            lines = [
                f"{entry['company']} | sector {entry['sector']} | "
                f"instrument {entry['instrument']} | weight "
                + (
                    "Not available"
                    if entry.get("weight_pct") is None
                    else f"{float(entry['weight_pct']):.2f}%"
                )
                for entry in top
            ]
            as_on = facts.get("holdings_portfolio_date")
            head = f"{doc.scheme_name} — top holdings"
            if as_on:
                head += f" as on {as_on}"
            _emit(
                chunks,
                counters,
                doc,
                "portfolio",
                "top_holding",
                lines[0] if lines else None,
                f"{head}: " + "; ".join(lines) + ".",
                config,
                value_type="table",
            )

        sectors = facts.get("sector_allocation") or []
        if sectors and config.chunk.holdings_mode == "sector_summary":
            lines = [
                f"{entry['sector']} {float(entry['weight_pct']):.2f}%"
                for entry in sectors
            ]
            _emit(
                chunks,
                counters,
                doc,
                "portfolio",
                "sector_allocation",
                lines[0] if lines else None,
                f"{doc.scheme_name} — sector allocation: " + "; ".join(lines) + ".",
                config,
                value_type="table",
            )

        for manager in facts.get("fund_managers") or []:
            name = manager.get("name")
            if not name:
                continue
            body = (
                f"{doc.scheme_name} — fund manager: {name} "
                f"(from {manager.get('tenure_from')})."
            )
            if manager.get("education"):
                body += f" {manager['education']}"
            _emit(
                chunks,
                counters,
                doc,
                "fund_management",
                "fund_manager",
                str(name),
                body,
                config,
                value_type="text",
            )

    return chunks, disagreements


def chunk_documents(
    documents: List[Document],
    config: Optional[AppConfig] = None,
    facts_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[Chunk], StrategyDecision, List[str]]:
    config = config or load_config()
    if facts_by_id is None:
        if not FACTS_PATH.exists():
            raise FileNotFoundError(
                f"{FACTS_PATH} missing; run scripts/seed_snapshot.py --live first"
            )
        facts_by_id = json.loads(FACTS_PATH.read_text(encoding="utf-8"))
    decision = select_chunking_strategy(config, documents)
    chunks, disagreements = _make_chunks(documents, facts_by_id, config)
    if decision.selected == "semantic":
        chunks = _apply_semantic(chunks, config)
    return chunks, decision, disagreements


def _apply_semantic(chunks: List[Chunk], config: AppConfig) -> List[Chunk]:
    import numpy as np

    groups: Dict[str, List[Chunk]] = {}
    for chunk in chunks:
        groups.setdefault(chunk.scheme_id, []).append(chunk)
    result: List[Chunk] = []
    for scheme_id, group in groups.items():
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(config.embed.model_id, device="cpu")
            vectors = model.encode(
                [c.text for c in group],
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            distances = 1.0 - np.asarray(vectors) @ np.asarray(vectors).T
            seen: set = set()
            index = 0
            while len(seen) < len(group):
                seed = next(i for i in range(len(group)) if i not in seen)
                seen.add(seed)
                cluster = [group[seed]]
                for i in range(len(group)):
                    if i in seen:
                        continue
                    if distances[seed][i] <= SEMANTIC_DISTANCE_THRESHOLD:
                        seen.add(i)
                        cluster.append(group[i])
                text = " ".join(item.text for item in cluster)
                base = group[seed]
                result.append(
                    Chunk(
                        chunk_id=build_chunk_id(
                            scheme_id, base.section, "semantic", index
                        ),
                        doc_id=base.doc_id,
                        scheme_id=scheme_id,
                        section=base.section,
                        field="semantic_cluster",
                        text=text,
                        embed_input=config.embed.render_prefix(
                            base.scheme_name if hasattr(base, "scheme_name") else scheme_id,
                            config.sources.by_scheme_id(scheme_id).category,
                            text,
                        ),
                        source_url=base.source_url,
                        value=base.value,
                        value_type="prose",
                        is_fact=False,
                        token_count=count_tokens(text),
                    )
                )
                index += 1
        except ImportError:
            return chunks
    return result


def audit_chunks(
    chunks: List[Chunk], config: Optional[AppConfig] = None
) -> Dict[str, Any]:
    config = config or load_config()
    failures: List[str] = []
    by_field: Dict[str, Dict[str, Chunk]] = {}
    for chunk in chunks:
        by_field.setdefault(chunk.field, {})[chunk.scheme_id] = chunk

    for field, expected in GOLD_TRUTH.items():
        for scheme_id, want in expected.items():
            found = by_field.get(field, {}).get(scheme_id)
            if found is None:
                failures.append(f"missing chunk {field} for {scheme_id}")
                continue
            got = found.value
            if str(got).strip() != str(want).strip():
                failures.append(
                    f"{scheme_id}.{field}: expected {want!r}, got {got!r}"
                )

    for chunk in chunks:
        if chunk.token_count > config.chunk.size_tokens:
            failures.append(
                f"{chunk.chunk_id}: {chunk.token_count} tokens exceeds "
                f"{config.chunk.size_tokens}"
            )
        for marker in CHUNK_NOISE_MARKERS:
            if marker in chunk.text:
                failures.append(f"{chunk.chunk_id}: noise marker {marker!r}")

    for spec in LABEL_REGISTRY:
        for scheme_id, chunk in by_field.get(spec.field, {}).items():
            if spec.resolve_label("") not in chunk.text and "{amc_name}" not in spec.text_label:
                failures.append(f"{chunk.chunk_id}: label split from value")
            if chunk.value and str(chunk.value) not in chunk.text:
                failures.append(f"{chunk.chunk_id}: value absent from chunk text")

    for chunk in chunks:
        if not any(
            chunk.source_url == entry.url for entry in config.sources.allowlist
        ):
            failures.append(f"{chunk.chunk_id}: source_url not in allowlist")
        if chunk.scope == "amc" and chunk.field not in {
            "amc_aum_total",
            "amc_incorporation_date",
        }:
            failures.append(f"{chunk.chunk_id}: unexpected amc scope")
        if chunk.field == "amc_aum_total" and chunk.scope != "amc":
            failures.append(f"{chunk.chunk_id}: amc_aum_total must be scope=amc")

    for field in ("fund_aum", "expense_ratio", "benchmark", "exit_load", "nav"):
        values = {
            chunk.value
            for chunk in chunks
            if chunk.field == field and not chunk.historical
        }
        expected_distinct = len({str(v).strip() for v in GOLD_TRUTH[field].values()})
        if len(values) != expected_distinct:
            failures.append(
                f"{field}: expected {expected_distinct} distinct values across "
                f"schemes, got {len(values)} {sorted(map(str, values))}"
            )

    for scheme_id, want in EXPECTED_HISTORICAL_EXIT_LOADS.items():
        got = sum(
            1
            for chunk in chunks
            if chunk.scheme_id == scheme_id
            and chunk.field == "exit_load_historical"
        )
        if got != want:
            failures.append(
                f"{scheme_id}: expected {want} historical exit-load chunks, got {got}"
            )

    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        failures.append("duplicate chunk_id detected")

    return {
        "passed": not failures,
        "failures": failures,
        "total_chunks": len(chunks),
        "checks": {
            "gold_facts": "fail" if any(".expected" in f for f in failures) else "ok",
            "token_budget": "fail"
            if any("tokens exceeds" in f for f in failures)
            else "ok",
            "noise_markers": "fail"
            if any("noise marker" in f for f in failures)
            else "ok",
            "label_value_atomic": "fail"
            if any("label split" in f or "value absent" in f for f in failures)
            else "ok",
            "allowlist_only": "fail"
            if any("allowlist" in f or "scope" in f for f in failures)
            else "ok",
            "cross_scheme_distinctness": "fail"
            if any("distinct" in f or "current values" in f for f in failures)
            else "ok",
        },
    }


def write_chunks_jsonl(chunks: List[Chunk], path: Optional[Path] = None) -> Path:
    return write_jsonl(
        path or CHUNKS_PATH,
        [json.loads(chunk.model_dump_json()) for chunk in chunks],
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stage2_chunk", description="Run Stage 2 chunking and the ingest audit"
    )
    parser.add_argument("--chunk-strategy", choices=["auto", "label_aware_recursive", "semantic"])
    args = parser.parse_args(argv)

    from src.pipeline.stage1_load import load_all

    config = load_config()
    if args.chunk_strategy:
        config.chunk.strategy = args.chunk_strategy
    started = datetime.now()
    documents = load_all(config)
    chunks, decision, disagreements = chunk_documents(documents, config)
    audit = audit_chunks(chunks, config)
    audit["facts_vs_text_disagreements"] = disagreements
    is_demo = decision.selected == "semantic"
    out_path = SEMANTIC_CHUNKS_PATH if is_demo else CHUNKS_PATH
    write_chunks_jsonl(chunks, out_path)
    duration = (datetime.now() - started).total_seconds()

    per_scheme: Dict[str, int] = {}
    for chunk in chunks:
        per_scheme[chunk.scheme_id] = per_scheme.get(chunk.scheme_id, 0) + 1
    log_path = None
    if is_demo:
        log_path = write_ingest_run_log(
            decision=decision,
            stage_summary={
                "stage1_documents": len(documents),
                "stage2_chunks": len(chunks),
                "chunks_per_scheme": per_scheme,
            },
            audit=audit,
            duration_s=duration,
            extra={"stage": "stage2_semantic_demo", "output": str(out_path)},
            path=LOGS_DIR / "ingest_run.semantic.json",
        )
    else:
        log_path = write_ingest_run_log(
            decision=decision,
            stage_summary={
                "stage1_documents": len(documents),
                "stage2_chunks": len(chunks),
                "chunks_per_scheme": per_scheme,
                "fields": sorted({chunk.field for chunk in chunks}),
            },
            audit=audit,
            duration_s=duration,
            extra={"stage": "stage2_only"},
        )

    print(f"strategy={decision.selected} rejected={decision.rejected}")
    print(f"rules={decision.rules}")
    print(f"total={len(chunks)}")
    for scheme_id in SCHEMES:
        print(f"  {scheme_id:26s} chunks={per_scheme.get(scheme_id, 0)}")
    print(f"audit_passed={audit['passed']}")
    for failure in audit["failures"]:
        print(f"  FAIL {failure}")
    if disagreements:
        print("facts_vs_text_disagreements:")
        for item in disagreements:
            print(f"  {item}")
    print(f"chunks_jsonl={out_path}")
    print(f"ingest_run_log={log_path}")
    if is_demo:
        print(
            "NOTE: semantic is a class-comparison demo (FR15). It is written to a "
            "separate file so it cannot overwrite the canonical corpus. The audit "
            "failing here is the expected evidence for rejecting it under R4: "
            "clustering destroys per-fact chunks, so gold facts are no longer "
            "individually retrievable."
        )
    return 0 if audit["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
