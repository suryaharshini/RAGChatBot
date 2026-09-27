"""Post-validator for every generated answer (architecture 7.6.1).

This module is the enforcement point for S6. The prompt asks the model to be
factual; the validator is what makes it so. An answer that fails here never
reaches the user, no matter how plausible it reads.

The ``grounded`` check is the load-bearing one: every numeric, currency,
percentage and date token in the answer must appear **verbatim** in the retrieved
chunks. That is what catches wrong-scheme attribution, which a citation check
alone cannot - a well-formed answer with a valid citation can still be about the
wrong fund.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.config import AppConfig
from src.models import RetrievedChunk, SchemeResolution, ValidationResult

LAST_UPDATED_PREFIX = "Last updated from sources:"

_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")

# Money, percentages, ISO and "01-Jan-2013" dates, bare numbers, and durations
# that carry a unit. Ordered so the longer forms win.
_TOKEN_RE = re.compile(
    r"₹\s?\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?\s?%"
    r"|\d{1,2}-[A-Za-z]{3}-\d{4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:Cr|crore|crores|BPS|bps)"
    r"|\b\d[\d,]*(?:\.\d+)?\b"
)

# Split on terminal punctuation followed by whitespace, but only when a sentence
# actually starts. "1.03%" and "groww.in" must not end a sentence - that is why
# this is not `count('.')`.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[]|$)")


def _chunk_text(chunk: Any) -> str:
    """Accept a RetrievedChunk, a Chunk, or a plain ``{"text": ...}`` dict."""
    if isinstance(chunk, RetrievedChunk):
        return chunk.chunk.text or ""
    if isinstance(chunk, dict):
        return str(chunk.get("text") or "")
    return str(getattr(chunk, "text", "") or "")


def strip_urls(text: str) -> str:
    return _URL_RE.sub(" ", text or "")


def sentence_count(text: str) -> int:
    """Real segmentation: masks URLs, then splits on sentence-final punctuation.

    A decimal like ``1.03%`` or a domain like ``groww.in`` is mid-token, so it
    never satisfies the lookahead that a real sentence break needs.
    """
    masked = strip_urls(text or "").strip()
    if not masked:
        return 0
    parts = [part for part in _SENTENCE_SPLIT_RE.split(masked) if part.strip()]
    return len(parts)


def extract_citations(text: str) -> List[str]:
    return _URL_RE.findall(text or "")


def extract_fact_tokens(text: str) -> List[str]:
    """Numeric, currency, percentage and date tokens, minus metadata noise."""
    body = strip_urls(text or "")
    # The "Last updated" date is provenance about the crawl, not a fact about
    # the fund, so it is exempt from the verbatim-evidence rule.
    body = re.sub(re.escape(LAST_UPDATED_PREFIX) + r"[^\n]*", " ", body, flags=re.I)
    return [match.group(0).strip() for match in _TOKEN_RE.finditer(body)]


def parse_last_updated(text: str) -> Optional[date]:
    match = re.search(
        re.escape(LAST_UPDATED_PREFIX) + r"\s*(\d{4}-\d{2}-\d{2})",
        text or "",
        flags=re.I,
    )
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _normalise(token: str) -> str:
    return re.sub(r"\s+", "", token).lower()


def _chunk_scheme_id(chunk: Any) -> Optional[str]:
    if isinstance(chunk, RetrievedChunk):
        return chunk.chunk.scheme_id
    if isinstance(chunk, dict):
        value = chunk.get("scheme_id")
        return str(value) if value else None
    value = getattr(getattr(chunk, "chunk", None), "scheme_id", None)
    return str(value) if value else None


def check_evidence_scheme(
    context_chunks: Sequence[Any],
    resolution: Optional[SchemeResolution],
) -> Dict[str, Any]:
    """The evidence must belong to the scheme the query resolved to.

    ``grounded`` on its own cannot catch wrong-scheme attribution when two
    schemes happen to publish the same value: "1% if redeemed within 1 year" is
    verbatim in a Large Cap chunk *and* a correct ELSS answer. Verifying the
    evidence's own ``scheme_id`` is what actually closes that hole, and it is the
    scenario architecture 9 asks to be fault-injected.

    Skipped when the chunks carry no ``scheme_id`` (plain text fixtures) or when
    the query is cross-scheme, since there is then no single expected scheme.
    """
    if resolution is None or not resolution.scheme_ids or resolution.ambiguous:
        return {"ok": True, "skipped": True}
    evidence_schemes = {
        scheme_id
        for scheme_id in (_chunk_scheme_id(chunk) for chunk in context_chunks)
        if scheme_id
    }
    if not evidence_schemes:
        return {"ok": True, "skipped": True}
    expected = set(resolution.scheme_ids)
    conflicts = evidence_schemes - expected
    return {
        "ok": not conflicts,
        "evidence_schemes": sorted(evidence_schemes),
        "expected_schemes": sorted(expected),
        "conflicts": sorted(conflicts),
    }


def check_grounded(
    answer_text: str,
    context_chunks: Sequence[Any],
    query: str = "",
) -> Dict[str, Any]:
    """Every fact token must appear verbatim in the retrieved evidence.

    Tokens echoed from the user's own question are excused, otherwise asking
    "what is the exit load in 12 months?" would make any answer containing
    "12" look ungrounded.
    """
    evidence = _normalise(" ".join(_chunk_text(chunk) for chunk in context_chunks))
    query_norm = _normalise(query or "")
    ungrounded: List[str] = []
    for token in extract_fact_tokens(answer_text):
        normalised = _normalise(token)
        if normalised in evidence:
            continue
        if normalised and normalised in query_norm:
            continue  # the user supplied it
        ungrounded.append(token)
    return {"ok": not ungrounded, "ungrounded": ungrounded}


def check_scheme_named(answer_text: str, resolution: Optional[SchemeResolution]) -> Dict[str, Any]:
    """The answer must name the scheme it was resolved to.

    Skipped when resolution is empty or ambiguous: a cross-scheme question like
    "what is the minimum SIP?" has no single scheme to name, and demanding one
    would force the model to invent an attribution.
    """
    if resolution is None or not resolution.scheme_ids or resolution.ambiguous:
        return {"ok": True, "skipped": True}
    haystack = (answer_text or "").lower()
    accepted: List[str] = []
    if resolution.matched_alias:
        accepted.append(resolution.matched_alias)
    for scheme_id in resolution.scheme_ids:
        accepted.append(scheme_id.replace("hdfc_", "").replace("_", " "))
        accepted.append(scheme_id.replace("hdfc_", ""))
    found = [candidate for candidate in accepted if candidate and candidate.lower() in haystack]
    return {"ok": bool(found), "accepted": accepted, "found": found}


def validate(
    answer_text: str,
    context_chunks: Sequence[Any],
    query: str,
    scheme_resolution: Optional[SchemeResolution],
    config: AppConfig,
) -> ValidationResult:
    """Run all eight checks from architecture 7.6.1."""
    from src.guards.intent import detect_advice
    from src.guards.pii import detect_pii
    from src.pipeline.stage1_load import url_is_allowed

    answer_text = answer_text or ""
    citations = extract_citations(answer_text)
    allowlist = [source.url for source in config.sources.allowlist]

    citation_present = len(citations) == 1
    if citation_present:
        citation_allowed = url_is_allowed(citations[0], config)
    else:
        citation_allowed = False

    grounded = check_grounded(answer_text, context_chunks, query)
    evidence_scheme = check_evidence_scheme(context_chunks, scheme_resolution)
    scheme = check_scheme_named(answer_text, scheme_resolution)
    # Layer 1 alone misses advice shapes the lexicon does not literally contain
    # ("you should buy"), so the output check also uses the structural signals.
    from src.guards.intent import _offline_classify

    no_advice = not detect_advice(answer_text) and _offline_classify(answer_text) != "advice"
    pii_hit = detect_pii(answer_text)
    sentences = sentence_count(answer_text)
    sentence_budget = sentences <= config.generate.max_sentences
    has_last_updated = LAST_UPDATED_PREFIX.lower() in answer_text.lower()

    checks: Dict[str, bool] = {
        "citation_present": citation_present,
        "citation_allowed": citation_allowed,
        "grounded": bool(grounded["ok"]),
        "evidence_scheme_match": bool(evidence_scheme["ok"]),
        "scheme_named": bool(scheme["ok"]),
        "no_advice": no_advice,
        "no_pii": pii_hit is None,
        "sentence_budget": sentence_budget,
        "has_last_updated": has_last_updated,
    }
    failures = [name for name, ok in checks.items() if not ok]

    details: Dict[str, Any] = {"sentences": sentences}
    if grounded.get("ungrounded"):
        details["ungrounded_tokens"] = grounded["ungrounded"]
    if not checks["evidence_scheme_match"] and not evidence_scheme.get("skipped"):
        details["evidence_schemes"] = evidence_scheme.get("evidence_schemes")
        details["expected_schemes"] = evidence_scheme.get("expected_schemes")
    if len(citations) > 1:
        details["citations"] = citations
    if not checks["no_pii"]:
        # The kind only. The matched value is never retained or logged.
        details["pii_kind"] = pii_hit.kind
    if not checks["scheme_named"] and not scheme.get("skipped"):
        details["scheme_expected"] = scheme.get("accepted")

    result = ValidationResult(
        passed=not failures,
        checks=checks,
        failures=failures,
        retry_count=0,
        details=details,
    )
    return result


# Checks that mean the answer is unsafe, not merely unpolished. architecture
# 7.6.1: no retry for these, discard immediately.
DISCARD_CHECKS = ("citation_allowed", "no_advice", "no_pii")


def is_discardable(result: ValidationResult) -> bool:
    return any(failure in DISCARD_CHECKS for failure in result.failures)


__all__ = [
    "LAST_UPDATED_PREFIX",
    "validate",
    "check_grounded",
    "check_scheme_named",
    "extract_citations",
    "extract_fact_tokens",
    "sentence_count",
    "parse_last_updated",
    "strip_urls",
    "is_discardable",
    "DISCARD_CHECKS",
]
