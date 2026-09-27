"""Stage 6: generate, validate, and return (architecture 7.6).

Order of operations, and the one rule that matters:

    1. Stage 5 runs first. A refusal or an ungrounded result short-circuits -
       no LLM is ever asked about a PII-bearing or advice-shaped query.
    2. Build the prompt from retrieved chunks only.
    3. Generate, then validate.
    4. On failure, retry once with stricter instructions.
    5. After the budget is spent, return the safe fallback plus the link.

An unvalidated answer is never returned (S6), including after the retry.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Optional, Tuple

from src.config import AppConfig
from src.guards.validator import (
    LAST_UPDATED_PREFIX,
    is_discardable,
    parse_last_updated,
    validate,
)
from src.llm.client import CONVERSATION_BLOCK, RETRY_INSTRUCTION, SYSTEM_PROMPT, LLMClient
from src.models import (
    Answer,
    Stage4Trace,
    Stage5Trace,
    Stage6Trace,
    Trace,
    ValidationResult,
)
from src.observability.trace import build_full_trace, stage5_trace, stage6_trace
from src.pipeline.stage5_retrieve import retrieve

FALLBACK_TEXT = (
    "I found the page but can't give a verified one-line answer. "
    "Please check the source directly."
)

NOT_AVAILABLE_TEXT = (
    "That detail isn't published on the source pages I can access, so I can't "
    "confirm it. Please check the source directly."
)


def _load_dotenv_once() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if os.environ.get("GROQ_API_KEY") or os.environ.get("LLM_API_KEY"):
        return
    load_dotenv()


def _fetched_at(config: AppConfig) -> date:
    """Crawl date, from the ingest run log. Never invented."""
    from src.observability.runlog import read_ingest_run_log

    try:
        log = read_ingest_run_log()
    except Exception:
        return date.today()
    stages = log.get("stages") or {}
    stamp = (stages.get("stage1") or {}).get("fetched_at")
    if not stamp:
        return date.today()
    try:
        return date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return date.today()


def _store_trace(config: AppConfig) -> Optional[Stage4Trace]:
    from src.pipeline.stage4_store import get_client, get_or_create_collection

    collection = get_or_create_collection(get_client(config), config)
    return Stage4Trace(
        collection=config.store.collection,
        n_chunks=collection.count(),
        metric=config.store.metric,
    )


def _build_system(
    config: AppConfig,
    context: str,
    fetched_at: date,
    query: str,
    history: Optional[list] = None,
) -> str:
    allowlist = "\n".join(f"- {source.url}" for source in config.sources.allowlist)
    system = SYSTEM_PROMPT.format(
        fetched_at=fetched_at.isoformat(),
        allowlist=allowlist,
        retrieved_chunks_with_source_urls=context,
        user_query=query,
    )
    if history:
        turns = "\n".join(
            f"Q: {turn['question']}\nA: {turn['answer']}" for turn in history
        )
        system = system + CONVERSATION_BLOCK.format(turns=turns)
    return system


def _scheme_hint_from_history(
    history: Optional[list], config: AppConfig
) -> Optional[list]:
    """Most recent unambiguous scheme mentioned in the conversation.

    Used only when the current query names no scheme itself. The first
    unambiguous turn wins; a genuinely cross-scheme question never matches, and
    an explicit scheme in the new query always takes precedence (the hint is
    applied inside ``retrieve`` only when resolution came back empty).
    """
    if not history:
        return None
    from src.retrieval.aliases import resolve_scheme

    for turn in reversed(history):
        question = str(turn.get("question") or "")
        if not question or question.startswith("[redacted"):
            continue
        resolution = resolve_scheme(question, config)
        if resolution.scheme_ids and not resolution.ambiguous:
            return list(resolution.scheme_ids)
    return None


def answer(
    query: str,
    config: Optional[AppConfig] = None,
    llm: Optional[LLMClient] = None,
    history: Optional[list] = None,
) -> Answer:
    """Produce a validated answer, a refusal, or the safe fallback.

    ``history`` is an optional list of prior ``{"question", "answer"}`` turns,
    used only to resolve references in the prompt. It never bypasses a guard and
    never reaches retrieval: the query the guards and the vector store see is
    exactly ``query``, unmodified. Callers must not put PII-bearing turns in it.
    """
    from src.config import load_config

    if config is None:
        config = load_config()
    if llm is None:
        from src.llm.client import get_llm

        llm = get_llm(config)

    _load_dotenv_once()

    # 1. Stage 5 owns the guards. Nothing reaches the LLM until it clears.
    outcome = retrieve(
        query,
        config=config,
        llm=llm,
        scheme_hint=_scheme_hint_from_history(history, config),
    )
    trace5 = stage5_trace(
        outcome.guard_decisions, outcome.scheme_resolution, outcome.selected
    )

    if outcome.refused:
        return Answer(
            text=outcome.refusal_text or "",
            refused=True,
            refusal_type=outcome.refusal_type,
            citation_url=None,
            last_updated=None,
            trace=build_full_trace(
                stage4=_store_trace(config), stage5=trace5, stage6=stage6_trace(None, None, 0),
            ),
        )

    if not outcome.grounded or not outcome.selected:
        link = _first_source_url(config)
        return Answer(
            text=NOT_AVAILABLE_TEXT,
            refused=False,
            citation_url=link,
            last_updated=_fetched_at(config),
            validation=ValidationResult(
                passed=True,
                checks={"grounded": False, "no_llm_call": True},
                failures=[],
                details={"reason": "stage5 similarity floor: nothing to answer from"},
            ),
            trace=build_full_trace(
                stage4=_store_trace(config), stage5=trace5, stage6=stage6_trace(None, None, 0),
            ),
        )

    # 2. Prompt.
    fetched_at = _fetched_at(config)
    system = _build_system(config, outcome.prompt_context, fetched_at, query, history)
    user = query

    # 3-5. Generate, validate, retry once, then fall back.
    attempts = max(1, int(config.generate.retry_on_validation_failure) + 1)
    best_result: Optional[ValidationResult] = None
    best_text: Optional[str] = None

    for attempt in range(attempts):
        try:
            text = llm.chat(system, user)
        except Exception as exc:
            # A provider failure must not surface a half-written answer.
            return Answer(
                text=FALLBACK_TEXT,
                citation_url=_first_source_url(config),
                last_updated=fetched_at,
                validation=ValidationResult(
                    passed=True,
                    checks={"llm_unavailable": True},
                    failures=[],
                    details={"error": type(exc).__name__},
                ),
                trace=build_full_trace(
                stage4=_store_trace(config), stage5=trace5, stage6=stage6_trace(None, None, attempt),
                ),
            )

        result = validate(
            text, outcome.selected, query, outcome.scheme_resolution, config
        )
        result.retry_count = attempt

        if result.passed:
            citation = _citation_of(text)
            return Answer(
                text=text.strip(),
                citation_url=citation,
                last_updated=parse_last_updated(text) or fetched_at,
                refused=False,
                validation=result,
                trace=build_full_trace(
                stage4=_store_trace(config), stage5=trace5, stage6=stage6_trace(citation, result, attempt),
                ),
            )

        if is_discardable(result):
            # citation_allowed / no_advice / no_pii: never retried. The result is
            # still recorded so the trace shows *why* the answer was dropped.
            best_result, best_text = result, text
            break

        best_result, best_text = result, text
        system = system + "\n\n" + RETRY_INSTRUCTION.format(
            reasons=", ".join(result.failures),
            fetched_at=fetched_at.isoformat(),
        )

    # 5. Safe fallback. The rejected text is never returned.
    # The link is re-checked against the allowlist: a discarded answer may have
    # carried an off-allowlist citation, and that URL must not reach the user.
    link = _safe_citation(best_text or "", config)
    fallback_validation = best_result or ValidationResult(passed=True, checks={})
    return Answer(
        text=f"{FALLBACK_TEXT}\n\nSource: {link}\n{LAST_UPDATED_PREFIX} {fetched_at.isoformat()}",
        citation_url=link,
        last_updated=fetched_at,
        refused=False,
        validation=fallback_validation,
        trace=build_full_trace(
                stage4=_store_trace(config), stage5=trace5, stage6=stage6_trace(link, fallback_validation, attempts - 1),
        ),
    )


def _citation_of(text: str) -> Optional[str]:
    from src.guards.validator import extract_citations

    citations = extract_citations(text)
    return citations[0] if citations else None


def _safe_citation(text: str, config: AppConfig) -> Optional[str]:
    """A citation is only reused if it is still on the allowlist.

    The text passed in may come from an answer that was discarded for citing an
    off-allowlist URL, which is exactly the case where trusting its link would
    defeat S6.
    """
    from src.pipeline.stage1_load import url_is_allowed

    candidate = _citation_of(text)
    if candidate and url_is_allowed(candidate, config):
        return candidate
    return _first_source_url(config)


def _first_source_url(config: AppConfig) -> Optional[str]:
    if not config.sources.allowlist:
        return None
    return config.sources.allowlist[0].url


__all__ = ["answer", "FALLBACK_TEXT", "NOT_AVAILABLE_TEXT"]
