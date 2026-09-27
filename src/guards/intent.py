from __future__ import annotations

import re
from typing import List, Optional, Pattern, Tuple

from src.config import AppConfig

EDUCATIONAL_LINK = "https://groww.in/mutual-funds"

# Refusals end with the bare URL and no trailing period. A citation extractor that
# splits on whitespace would otherwise capture "https://groww.in/mutual-funds."
# and S6 would reject it as a URL outside the allowlist. Phase 6's validator must
# still strip trailing punctuation, but the text no longer relies on it.
REFUSAL_ADVICE = (
    "I only share published facts about HDFC Mutual Fund schemes, so I can't tell you "
    "whether to buy, sell, or hold anything. For a general primer on how these funds "
    f"work: {EDUCATIONAL_LINK}"
)

ADVICE_LEXICON: List[str] = [
    "should i buy",
    "should i sell",
    "should i invest",
    "should i redeem",
    "which is better",
    "what should i",
    "is it a good time",
    "is now a good time",
    "good time to",
    "recommend",
    "suggest",
    "worth it",
    "best fund for",
    "build me a portfolio",
    "exit or hold",
    "hold or exit",
    "buy or sell",
    "the best one",
    "which of these",
    "best scheme",
    "good pick",
    "safe to invest",
    "can i invest in",
]

FOREIGN_AMCS: List[str] = [
    "sbi", "kotak", "icici", "axis", "nippon", "franklin", "parag parikh", "ppfas",
    "motilal", "quant", "bandhan", "canara", "baroda", "union", "lic", "principal",
    "invesco", "mirae", "aditya", "sundaram", "shriram", "l&t", "hsbc", "idbi",
    "jm financial", "tata", "groww mf", "parag", "max", "sbi mf", "nippon india",
]

_ADVICE_PATTERNS: List[Pattern] = [
    re.compile(p) for p in ADVICE_LEXICON
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def detect_advice(text: str) -> bool:
    haystack = _normalize(text)
    return any(pattern.search(haystack) for pattern in _ADVICE_PATTERNS)


def detect_foreign_amc(text: str) -> Optional[str]:
    haystack = _normalize(text)
    for amc in FOREIGN_AMCS:
        if re.search(rf"\b{re.escape(amc)}\b", haystack):
            return amc
    return None


# Layer 2 must be independent of ADVICE_LEXICON or "agreement" is a tautology.
# These are structural signals: a decision modal followed by an action verb, a
# superlative over a set of schemes, or a return-projection frame. The offline
# classifier reads shape and grammar, not the published phrase list.
_OFFLINE_ADVICE_SIGNALS: List[Pattern] = [
    # "should I buy", "can I sell", "do I hold" - modal + decision verb
    re.compile(r"\b(should|can|could|would|do|will)\s+i\s+(\w+\s+){0,2}?"
               r"(buy|sell|hold|redeem|invest|exit|switch|enter|continue|avg|average)\b"),
    # "is it a good time", "is now a good idea", "was it worth buying"
    re.compile(r"\b(good|right|ideal|bad|poor)\s+(time|idea|entry|exit)\b"),
    re.compile(r"\bworth\s+(it|buying|investing|it\s+to)\b"),
    # "which one", "which of these", "the best scheme" - superlative over a set
    re.compile(r"\b(best|top|better|optimal|safest|most\s+\w+)\b[^?]{0,40}?"
               r"\b(fund|scheme|plan|option|one|which)\b"),
    # "which one", "which of these" - only advice when asking us to *judge* a
    # set. "Which of these funds has a lock-in period?" is a factual lookup, so
    # an evaluative tail is required rather than the interrogative alone.
    re.compile(r"\bwhich\s+(of\s+these|one|is)\b[^?]{0,40}?"
               r"\b(best|good|better|hold|suitable|worth|recommend|prefer|choose|pick)\b"),
    # directives aimed at the user
    re.compile(r"\b(you\s+should|go\s+for|opt\s+for|you\s+can\s+consider)\b"),
    # "can I invest in" was in the lexicon; keep the structural form too
    re.compile(r"\bcan\s+i\s+(invest|put\s+money)\b"),
]

_OFFLINE_PERFORMANCE_SIGNALS: List[Pattern] = [
    re.compile(r"\b(what|how\s+much)\b[^?]{0,40}?\b(will|would|could|should)\b"
               r"[^?]{0,40}?\b(return|grow|gain|earn|appreciate|be\s+worth)\b"),
    re.compile(r"\b(returns?|performance|profit|gain|loss|yield|growth)\b"
               r"[^?]{0,30}?\b(in|over|after)\b[^?]{0,20}?\b(year|yr|month|5\s*year|10\s*year)\b"),
    re.compile(r"\b(compare|versus|vs\.?|outperform|beat|against)\b[^?]{0,40}?"
               r"\b(return|performance|fund|scheme)\w*\b"),
    re.compile(r"\b(expected|projected|likely|estimated)\b[^?]{0,30}?\b(return|gain|growth)\b"),
    re.compile(r"\bhow\s+(much|many)\b[^?]{0,40}?\b(make|earn|gain)\b"),
    re.compile(r"\bsafe\s+for\b[^?]{0,20}?\b\d+\s*(year|month)"),
    re.compile(r"\b(risk[- ]adjusted\s+)?returns?\b[^?]{0,20}?\bvs\b"),
]


def _offline_classify(text: str) -> str:
    """A real second opinion, not a re-run of layer 1.

    Deliberately built from grammar and query shape so it can disagree with the
    lexicon. That is the point: "What should I know about the exit load?" trips
    the ``what should i`` lexicon entry but is a factual question, and an
    independent classifier correctly says so, so nothing is refused.
    """
    haystack = _normalize(text)
    if detect_foreign_amc(text):
        return "out_of_scope"
    if any(pattern.search(haystack) for pattern in _OFFLINE_ADVICE_SIGNALS):
        return "advice"
    if any(pattern.search(haystack) for pattern in _OFFLINE_PERFORMANCE_SIGNALS):
        return "performance"
    return "factual"


def classify_intent_zeroshot(text: str, llm: Optional[object] = None) -> str:
    """Layer 2 of the advice/performance guards.

    With an LLM adapter this is the real zero-shot classifier. Without one it
    falls back to :func:`_offline_classify`, an independent structural classifier,
    so the two layers can genuinely agree or disagree. The lexicon is the only
    layer that consults ``ADVICE_LEXICON``/``PERFORMANCE_LEXICON``.
    """
    if llm is not None:
        classifier = getattr(llm, "classify_intent", None)
        if callable(classifier):
            try:
                return str(classifier(text))
            except Exception:
                pass
    return _offline_classify(text)


def _two_layer(
    layer1: bool,
    text: str,
    label: str,
    llm: Optional[object],
) -> Tuple[bool, str]:
    if not layer1:
        return False, f"layer1_{label}_lexicon: no match"
    layer2 = classify_intent_zeroshot(text, llm)
    if layer2 == label:
        source = "classifier" if llm is not None else "offline structural classifier"
        return True, f"layer1_{label}_lexicon: match; layer2 {source} agreed ({layer2})"
    return False, (
        f"layer1_{label}_lexicon: match but layer2 classified {layer2}; "
        "not blocking because the layers must agree"
    )


def detect_advice_two_layer(text: str, llm: Optional[object] = None) -> Tuple[bool, str]:
    return _two_layer(detect_advice(text), text, "advice", llm)


def detect_performance_two_layer(text: str, llm: Optional[object] = None) -> Tuple[bool, str]:
    from src.guards.performance import detect_performance

    return _two_layer(detect_performance(text), text, "performance", llm)


def refusal_for_advice() -> str:
    return REFUSAL_ADVICE


def in_scope_scheme_names(config: AppConfig) -> List[str]:
    return [entry.scheme_name for entry in config.sources.allowlist]


def build_out_of_corpus_refusal(config: AppConfig) -> str:
    names = in_scope_scheme_names(config)
    listed = "; ".join(names)
    return (
        f"I only have facts for 5 HDFC Mutual Fund schemes, so I can't answer that. "
        f"In scope: {listed}. Each is Direct Plan Growth only."
    )


def detect_out_of_corpus(text: str, config: AppConfig) -> Tuple[bool, Optional[str]]:
    foreign = detect_foreign_amc(text)
    if foreign:
        return True, foreign
    from src.retrieval.aliases import resolve_scheme

    resolution = resolve_scheme(text, config)
    if resolution.scheme_ids:
        return False, None
    haystack = _normalize(text)
    scheme_words = ("cap", "fund", "elss", "tax saver", "balanced", "advantage", "equity")
    if any(word in haystack for word in scheme_words):
        return True, "no in-scope scheme matched"
    return False, None


__all__ = [
    "ADVICE_LEXICON",
    "EDUCATIONAL_LINK",
    "FOREIGN_AMCS",
    "REFUSAL_ADVICE",
    "build_out_of_corpus_refusal",
    "classify_intent_zeroshot",
    "detect_advice",
    "detect_advice_two_layer",
    "detect_foreign_amc",
    "detect_out_of_corpus",
    "in_scope_scheme_names",
    "refusal_for_advice",
]
