from __future__ import annotations

import re
from typing import List, Optional, Pattern, Tuple

from src.guards.intent import EDUCATIONAL_LINK

# S3/FR9 ask the performance refusal to link the official factsheet, but S4/S6
# constrain every citation to the 5 allowlisted URLs and the corpus contains no
# factsheet URL. Inventing an hdfcmf.com factsheet link would be a fabricated
# citation, so the refusal names the official factsheet as the place to look and
# cites the allowlisted page we are actually permitted to cite. Recorded as a
# deliberate deviation in implementation.md.
REFUSAL_PERFORMANCE = (
    "I don't compute or compare returns, and I can't forecast future performance. "
    "For audited, official performance figures, check the fund's official factsheet "
    "published by HDFC Mutual Fund. Past performance is published on the scheme "
    f"page: {EDUCATIONAL_LINK}"
)

PERFORMANCE_LEXICON: List[str] = [
    "will it return",
    "expected return",
    "projected",
    "which performed better",
    "compare returns",
    "outperform",
    "top performing",
    "return in 5 years",
    "returns in 5 years",
    "performance comparison",
    "which is better performing",
    "past performance",
    "how much will",
    "how much does it return",
    "expected to return",
    "will it give",
    "profit",
]

_RETURN_WORDS = r"(?:return|returns|yield|gain|gains|performance|profit|appreciation)"
_PERFORMANCE_PATTERNS: List[Pattern] = [re.compile(p) for p in PERFORMANCE_LEXICON] + [
    re.compile(rf"\bwhat will\b.*{_RETURN_WORDS}"),
    re.compile(rf"\bwill\b.*{_RETURN_WORDS}\b"),
    re.compile(rf"\bcompare\b.{{0,40}}{_RETURN_WORDS}"),
    re.compile(rf"\b{_RETURN_WORDS}\b.{{0,20}}\bvs\b"),
    re.compile(rf"\bvs\b.{{0,40}}{_RETURN_WORDS}"),
    re.compile(rf"\b{_RETURN_WORDS}\b.{{0,30}}\d+\s*%"),
    re.compile(r"\b\d+\s*%\s*(?:p\.?a\.?)?\s*" + _RETURN_WORDS),
    re.compile(r"\bexpected\b.{0,30}\b" + _RETURN_WORDS),
    re.compile(rf"\bhow much\b.{{0,40}}{_RETURN_WORDS}"),
    re.compile(r"\bprojected\b"),
    re.compile(rf"\bwhich\b.{{0,40}}{_RETURN_WORDS}"),
]

def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def detect_performance(text: str) -> bool:
    haystack = _normalize(text)
    return any(pattern.search(haystack) for pattern in _PERFORMANCE_PATTERNS)


def detect_performance_two_layer(
    text: str, llm: Optional[object] = None
) -> Tuple[bool, str]:
    from src.guards.intent import classify_intent_zeroshot

    layer1 = detect_performance(text)
    if not layer1:
        return False, "layer1_performance_lexicon: no match"
    layer2 = classify_intent_zeroshot(text, llm)
    if llm is None:
        return True, (
            "layer1_performance_lexicon: match; layer2 unavailable (no LLM adapter), "
            "blocking on layer1 alone"
        )
    if layer2 == "performance":
        return True, f"layer1_performance_lexicon: match; layer2 agreed ({layer2})"
    return False, (
        f"layer1_performance_lexicon: match but layer2 classified {layer2}; "
        "not blocking because the layers must agree"
    )


def refusal_for_performance(factsheet_url: Optional[str] = None) -> str:
    if factsheet_url:
        return REFUSAL_PERFORMANCE.replace(EDUCATIONAL_LINK, factsheet_url)
    return REFUSAL_PERFORMANCE


__all__ = [
    "PERFORMANCE_LEXICON",
    "REFUSAL_PERFORMANCE",
    "detect_performance",
    "detect_performance_two_layer",
    "refusal_for_performance",
]
