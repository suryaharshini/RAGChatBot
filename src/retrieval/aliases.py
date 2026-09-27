from __future__ import annotations

import re
from typing import Dict, List, Tuple

from src.config import AppConfig
from src.models import SchemeResolution

SCHEME_ALIASES: Dict[str, List[str]] = {
    "hdfc_large_cap": [
        "large cap",
        "hdfc large cap fund",
        "largecap fund",
    ],
    "hdfc_flexi_cap": [
        "flexi cap",
        "equity fund",
        "hdfc equity fund",
        "flexi cap fund",
    ],
    "hdfc_elss": [
        "elss",
        "tax saver",
        "80c",
        "80c tax saver",
        "tax saver fund",
        "elss tax saver",
    ],
    "hdfc_small_cap": [
        "small cap",
        "smallcap fund",
    ],
    "hdfc_balanced_advantage": [
        "balanced advantage",
        "balanced",
        "balanced advantage fund",
    ],
}

_ALIAS_TO_SCHEME: Dict[str, str] = {
    alias: scheme_id
    for scheme_id, aliases in SCHEME_ALIASES.items()
    for alias in aliases
}

# Longest aliases first so "balanced advantage" wins over "balanced" and
# "hdfc equity fund" wins over "equity fund".
_ORDERED_ALIASES: List[Tuple[str, str]] = sorted(
    _ALIAS_TO_SCHEME.items(), key=lambda kv: len(kv[0]), reverse=True
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _alias_pattern(alias: str) -> re.Pattern:
    escaped = re.escape(alias).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")


_COMPILED = [(alias, scheme_id, _alias_pattern(alias)) for alias, scheme_id in _ORDERED_ALIASES]


def resolve_scheme(text: str, config: AppConfig) -> SchemeResolution:
    haystack = _normalize(text)

    from src.guards.intent import detect_foreign_amc

    foreign = detect_foreign_amc(haystack)
    if foreign:
        return SchemeResolution(
            scheme_ids=[],
            matched_alias=None,
            confidence=0.0,
            ambiguous=True,
        )

    matched: List[str] = []
    matched_alias: List[str] = []
    consumed: List[Tuple[int, int]] = []
    for alias, scheme_id, pattern in _COMPILED:
        for match in pattern.finditer(haystack):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in consumed):
                continue
            consumed.append(span)
            if scheme_id not in matched:
                matched.append(scheme_id)
            matched_alias.append(alias)
            break

    if not matched:
        return SchemeResolution(
            scheme_ids=[], matched_alias=None, confidence=0.0, ambiguous=True
        )

    in_scope = {entry.scheme_id for entry in config.sources.allowlist}
    matched = [scheme_id for scheme_id in matched if scheme_id in in_scope]
    if not matched:
        return SchemeResolution(
            scheme_ids=[], matched_alias=None, confidence=0.0, ambiguous=True
        )

    if len(matched) == 1:
        return SchemeResolution(
            scheme_ids=matched,
            matched_alias=matched_alias[0],
            confidence=0.95,
            ambiguous=False,
        )
    return SchemeResolution(
        scheme_ids=matched,
        matched_alias=",".join(matched_alias),
        confidence=0.6,
        ambiguous=True,
    )


def scheme_filter(resolution: SchemeResolution) -> Dict[str, object]:
    if not resolution.scheme_ids:
        return {}
    if len(resolution.scheme_ids) == 1:
        return {"scheme_id": resolution.scheme_ids[0]}
    return {"scheme_id": {"$in": list(resolution.scheme_ids)}}


__all__ = ["SCHEME_ALIASES", "resolve_scheme", "scheme_filter"]
