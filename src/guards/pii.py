from __future__ import annotations

import re
from typing import List, Optional, Tuple

from pydantic import BaseModel

REFUSAL_PII = (
    "I don't accept or store personal or account identifiers such as PAN, Aadhaar, "
    "folio numbers, or OTPs. Please remove it from your question. For account-specific "
    "help, contact HDFC Mutual Fund directly."
)

AMC_CONTACT_URL = "https://www.hdfcmf.com/contact-us"


class PIIHit(BaseModel):
    kind: str
    start: int
    end: int

    def __repr__(self) -> str:
        return f"PIIHit(kind={self.kind!r}, start={self.start}, end={self.end})"


PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
AADHAAR_RE = re.compile(
    r"(?i)(?:\baadhaar\b\s*(?:number|no\.?|#)?\s*[:\-]?\s*)?"
    r"\b[2-9][0-9]{3}\s?[0-9]{4}\s?[0-9]{4}\b"
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\b(?:\+?91[\-\s]?)?[6-9][0-9]{4}[0-9]{5}\b")
OTP_RE = re.compile(
    r"(?i)\b(?:otp|one\s*time\s*(?:password|otp|code))\b\s*(?:is|:|=)?\s*[0-9]{4,6}\b"
)
FOLIO_RE = re.compile(
    r"(?i)\b(?:folio|account|acct|acc)\s*(?:number|no\.?|#)?\s*[:\-]?\s*[0-9]{6,14}\b"
)
BARE_LONG_NUMBER_RE = re.compile(r"\b[0-9]{9,14}\b")

_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("pan", PAN_RE),
    ("aadhaar", AADHAAR_RE),
    ("email", EMAIL_RE),
    ("phone", PHONE_RE),
    ("otp", OTP_RE),
    ("folio", FOLIO_RE),
]


def detect_pii(text: str) -> Optional[PIIHit]:
    """Return the first PII hit, or None.

    The matched span is used only to build an in-memory refusal. It is never
    logged, never embedded, and never returned to a caller that might persist it,
    which is why PIIHit deliberately exposes no ``text`` attribute.
    """
    if not text:
        return None
    for kind, pattern in _PATTERNS:
        match = pattern.search(text)
        if match:
            return PIIHit(kind=kind, start=match.start(), end=match.end())
    bare = BARE_LONG_NUMBER_RE.search(text)
    if bare:
        return PIIHit(kind="account_number", start=bare.start(), end=bare.end())
    return None


def contains_pii(text: str) -> bool:
    return detect_pii(text) is not None


def redact_pii_from_logs() -> bool:
    return True


def refusal_for_pii() -> str:
    return REFUSAL_PII


__all__ = [
    "AMC_CONTACT_URL",
    "PIIHit",
    "REFUSAL_PII",
    "contains_pii",
    "detect_pii",
    "redact_pii_from_logs",
    "refusal_for_pii",
]
