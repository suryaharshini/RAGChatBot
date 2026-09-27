from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

PLAN = "Direct Growth"
EMBED_TOKEN_BUDGET = 230
CHUNK_NOISE_MARKERS = (
    "Compare similar funds",
    "Return calculator",
    "Understand terms",
)


class Document(BaseModel):
    doc_id: str
    scheme_id: str
    scheme_name: str
    plan: str = PLAN
    category: str
    source_url: str
    fetched_at: date
    text: str
    text_sha256: str

    @field_validator("plan")
    @classmethod
    def plan_is_supported(cls, value: str) -> str:
        if value != PLAN:
            raise ValueError(f"only {PLAN!r} is supported in this corpus, got {value!r}")
        return value


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    scheme_id: str
    section: str
    field: str
    text: str
    embed_input: str
    source_url: str
    value: Optional[str] = None
    value_type: str = "text"
    scope: str = "fund"
    effective_date: Optional[date] = None
    historical: bool = False
    is_fact: bool = False
    token_count: int = 0

    @field_validator("embed_input")
    @classmethod
    def embed_input_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("embed_input must not be blank")
        return value


@dataclass
class ChunkParams:
    strategy: str
    size_tokens: int
    overlap_tokens: int
    separators: List[str]
    never_split: List[str]
    holdings_mode: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "size_tokens": self.size_tokens,
            "overlap_tokens": self.overlap_tokens,
            "separators": list(self.separators),
            "never_split": list(self.never_split),
            "holdings_mode": self.holdings_mode,
        }


@dataclass
class StrategyDecision:
    selected: str
    rejected: List[str]
    rules: Dict[str, bool]
    rationale: str
    params: ChunkParams
    corpus_stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selected": self.selected,
            "rejected": list(self.rejected),
            "rules": dict(self.rules),
            "rationale": self.rationale,
            "params": self.params.to_dict(),
            "corpus_stats": dict(self.corpus_stats),
        }


class GuardDecision(BaseModel):
    guard: str
    triggered: bool
    reason: Optional[str] = None
    refusal_text: Optional[str] = None
    loggable: bool = True

    @field_validator("loggable")
    @classmethod
    def pii_never_loggable(cls, value: bool, info) -> bool:
        if info.data.get("guard") == "pii" and value:
            raise ValueError("pii guard decisions must never be loggable")
        return value


class SchemeResolution(BaseModel):
    scheme_ids: List[str] = Field(default_factory=list)
    matched_alias: Optional[str] = None
    confidence: float = 0.0
    ambiguous: bool = True


class RetrievedChunk(BaseModel):
    chunk: Chunk
    cosine_distance: float
    rank: int = 0


class ValidationResult(BaseModel):
    passed: bool
    checks: Dict[str, bool] = Field(default_factory=dict)
    failures: List[str] = Field(default_factory=list)
    retry_count: int = 0
    details: Dict[str, Any] = Field(default_factory=dict)

    @property
    def failed_checks(self) -> List[str]:
        return list(self.failures)


class Answer(BaseModel):
    text: str
    citation_url: Optional[str] = None
    last_updated: Optional[date] = None
    refused: bool = False
    refusal_type: Optional[str] = None
    validation: Optional[ValidationResult] = None
    trace: Optional["Trace"] = None


class Stage1Trace(BaseModel):
    source_url: str
    fetched_at: date
    text_sha256: str
    chars: int


class Stage2Trace(BaseModel):
    chunk_ids: List[str] = Field(default_factory=list)
    token_counts: List[int] = Field(default_factory=list)
    strategy: Optional[Dict[str, Any]] = None


class Stage3Trace(BaseModel):
    model_id: str
    dim: int
    truncation_assert_passed: bool
    cache_hit: bool = False


class Stage4Trace(BaseModel):
    collection: str
    n_chunks: int
    metric: str


class Stage5Trace(BaseModel):
    guard_decisions: List[GuardDecision] = Field(default_factory=list)
    scheme_resolution: Optional[SchemeResolution] = None
    top_chunks: List[Dict[str, Any]] = Field(default_factory=list)


class Stage6Trace(BaseModel):
    citation_url: Optional[str] = None
    validation: Optional[ValidationResult] = None
    retry_count: int = 0


class Trace(BaseModel):
    stage1: Optional[Stage1Trace] = None
    stage2: Optional[Stage2Trace] = None
    stage3: Optional[Stage3Trace] = None
    stage4: Optional[Stage4Trace] = None
    stage5: Optional[Stage5Trace] = None
    stage6: Optional[Stage6Trace] = None

    def is_complete(self) -> bool:
        return all(
            getattr(self, f"stage{index}") is not None for index in range(1, 7)
        )


class RetrievalOutcome(BaseModel):
    query: str
    refused: bool = False
    refusal_type: Optional[str] = None
    refusal_text: Optional[str] = None
    guard_decisions: List[GuardDecision] = Field(default_factory=list)
    scheme_resolution: Optional[SchemeResolution] = None
    candidates: List[RetrievedChunk] = Field(default_factory=list)
    selected: List[RetrievedChunk] = Field(default_factory=list)
    grounded: bool = False
    prompt_context: str = ""


class Stage3Result(BaseModel):
    vectors: Any
    model_id: str
    dim: int
    truncation_assert_passed: bool
    cache_hit: bool = False

    model_config = {"arbitrary_types_allowed": True}


class StoreResult(BaseModel):
    collection: str
    upserted: int
    total_in_collection: int
    metric: str


class IngestError(Exception):
    pass


class AllowlistViolation(IngestError):
    pass


class FetchError(IngestError):
    pass


class ExtractionError(IngestError):
    pass


class GuardTripped(Exception):
    def __init__(self, guard: str, reason: str, refusal_text: str) -> None:
        super().__init__(reason)
        self.guard = guard
        self.reason = reason
        self.refusal_text = refusal_text


class ValidationFailed(Exception):
    def __init__(self, failures: List[str]) -> None:
        super().__init__("; ".join(failures))
        self.failures = list(failures)


Answer.model_rebuild()


__all__ = [
    "CHUNK_NOISE_MARKERS",
    "EMBED_TOKEN_BUDGET",
    "PLAN",
    "AllowlistViolation",
    "Answer",
    "Chunk",
    "ChunkParams",
    "Document",
    "ExtractionError",
    "FetchError",
    "GuardDecision",
    "GuardTripped",
    "IngestError",
    "RetrievalOutcome",
    "RetrievedChunk",
    "SchemeResolution",
    "Stage1Trace",
    "Stage2Trace",
    "Stage3Result",
    "Stage3Trace",
    "Stage4Trace",
    "Stage5Trace",
    "Stage6Trace",
    "StoreResult",
    "StrategyDecision",
    "Trace",
    "ValidationFailed",
    "ValidationResult",
]
