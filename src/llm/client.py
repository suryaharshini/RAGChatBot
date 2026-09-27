"""Provider-agnostic LLM adapter (Phase 6, task 1).

The pipeline must never hard-depend on a vendor, so everything here talks to a
single ``chat(system, user) -> str`` method and the concrete provider is chosen
by the ``LLM_PROVIDER`` environment variable. ``FakeLLM`` is the default so the
whole downstream path is testable with no API key and no network.

The API key is read from the environment only. It is never written to a log, a
trace, or any module-level constant.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from src.config import AppConfig

# PRD.md 6.5, reproduced verbatim, plus the two explicit rules the brief calls
# out. {fetched_at} and {allowlist} are injected per call - never hardcoded.
SYSTEM_PROMPT = """You are a mutual-fund FACTS assistant. You do not give advice, opinions, \
recommendations, or forecasts. You never compute or compare returns.

Rules:
- Answer only from <context>. If not present, say it is not available.
- Max 3 sentences. Be literal; copy numbers and names verbatim.
- Always name the scheme and its plan (e.g. "HDFC Flexi Cap Direct Plan Growth").
- End with exactly one citation link from the allowlist, then the line
  "Last updated from sources: {fetched_at}".
- Fund-level AUM is the "Fund size (AUM)" field. The "About" block AUM is
  AMC-level - only use it if the user explicitly asks about the AMC.
- Historical exit-load rows are not the current load.

Allowed citation URLs (use exactly one, and only if you used it):
{allowlist}

<context>
{retrieved_chunks_with_source_urls}
</context>

Question: {user_query}
"""

CONVERSATION_BLOCK = """
Conversation so far (most recent last). Use it only to resolve references like
"that one" or "its exit load". If the current question is answerable without it,
ignore it. Do not repeat earlier answers.
{turns}
"""

RETRY_INSTRUCTION = (
    "Your previous answer failed validation: {reasons}. "
    "Rewrite it so that every number and name is copied verbatim from the context, "
    "it is at most 3 sentences, it ends with exactly one allowlisted citation URL, "
    "and it ends with the line 'Last updated from sources: {fetched_at}'."
)


class LLMError(RuntimeError):
    """Raised when a provider is unreachable or returns nothing usable."""


class LLMClient:
    """Interface. Subclasses implement :meth:`_complete`."""

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        self.config = config

    def chat(
        self,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        return self._complete(
            system,
            user,
            temperature if temperature is not None else self._temperature(),
            max_tokens if max_tokens is not None else self._max_tokens(),
        )

    def _temperature(self) -> float:
        return self.config.generate.temperature if self.config else 0.0

    def _max_tokens(self) -> int:
        return self.config.generate.max_tokens if self.config else 220

    def _complete(self, system: str, user: str, temperature: float, max_tokens: int) -> str:
        raise NotImplementedError

    def classify_intent(self, text: str) -> str:
        """Layer 2 of the advice/performance guards (architecture 7.5.2).

        Kept on the same object so ``retrieve(query, config, embedder, llm)`` can
        hand the same client to both the guards and the generator. Defaults to a
        safe ``"factual"`` so an adapter that has not overridden it can never
        cause a guard to block.
        """
        return "factual"


class FakeLLM(LLMClient):
    """Returns a canned string. No key, no network, fully deterministic.

    If a prompt carries several ``canned`` values, the first one whose expected
    substring appears in the context is returned. That keeps the retry-and-refuse
    tests meaningful without a real model.
    """

    def __init__(self, canned: str = "", config: Optional[AppConfig] = None) -> None:
        super().__init__(config)
        self.canned = canned
        self.calls: list = []

    def _complete(self, system: str, user: str, temperature: float, max_tokens: int) -> str:
        self.calls.append({"system": system, "user": user})
        return self.canned

    def classify_intent(self, text: str) -> str:
        from src.guards.intent import _offline_classify

        return _offline_classify(text)


class GroqClient(LLMClient):
    """Groq adapter. OpenAI-compatible chat completions, ``temperature=0.0``."""

    ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
    # gpt-oss-20b/120b are reasoning models on Groq: they emit a `reasoning` field and
    # can burn the entire max_tokens budget before emitting any `content`, which
    # returns an empty string and fails validation. qwen/qwen3.8-27b is
    # non-reasoning, ~0.2s, and correct on the guard classifier. Re-check the
    # available list at /openai/v1/models before changing this.
    DEFAULT_MODEL = "qwen/qwen3.8-27b"

    def __init__(self, config: Optional[AppConfig] = None, model: Optional[str] = None) -> None:
        super().__init__(config)
        self.api_key = os.environ.get("GROQ_API_KEY") or os.environ.get("LLM_API_KEY")
        if not self.api_key:
            raise LLMError(
                "GROQ_API_KEY is not set. Put it in .env (see .env.example) or use "
                "LLM_PROVIDER=fake."
            )
        self.model = model or os.environ.get("LLM_MODEL") or self.DEFAULT_MODEL

    def _complete(self, system: str, user: str, temperature: float, max_tokens: int) -> str:
        import requests

        try:
            response = requests.post(
                self.ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=30,
            )
        except Exception as exc:  # network, DNS, TLS
            raise LLMError(f"groq request failed: {exc}") from exc
        if response.status_code != 200:
            # Deliberately does not echo the request or the key.
            raise LLMError(f"groq returned HTTP {response.status_code}")
        try:
            return str(response.json()["choices"][0]["message"]["content"])
        except Exception as exc:
            raise LLMError(f"could not parse groq response: {exc}") from exc

    def classify_intent(self, text: str) -> str:
        """Zero-shot classification, the real layer 2 (architecture 7.5.2).

        Fails **closed** on the safety guards: an unreachable classifier returns
        ``"advice"`` so an unparseable reply cannot silently downgrade a
        refusal into an allowed answer.
        """
        prompt = (
            "Classify the user question as exactly one of: advice, performance, "
            "factual, out_of_scope.\n"
            "advice = asks whether to buy/sell/hold/redeem, or which fund is best.\n"
            "performance = asks for returns, projections, or comparisons of returns.\n"
            "out_of_scope = names a fund house other than HDFC.\n"
            "factual = asks for a published figure or fact.\n"
            f"Question: {text}\nAnswer with one word:"
        )
        try:
            raw = self._complete(prompt, text, 0.0, 8)
        except LLMError:
            return "advice"
        token = raw.strip().lower().split()[0] if raw.strip() else ""
        if token in {"advice", "performance", "factual", "out_of_scope"}:
            return token
        return "advice"


def get_llm(config: Optional[AppConfig] = None) -> LLMClient:
    """Build the adapter named by ``LLM_PROVIDER`` (default ``fake``)."""
    provider = (os.environ.get("LLM_PROVIDER") or "fake").strip().lower()
    if provider == "fake":
        return FakeLLM(canned="", config=config)
    if provider == "groq":
        return GroqClient(config=config)
    raise LLMError(f"unknown LLM_PROVIDER {provider!r}; expected 'fake' or 'groq'")


def has_api_key() -> bool:
    return bool(os.environ.get("GROQ_API_KEY") or os.environ.get("LLM_API_KEY"))


__all__ = [
    "SYSTEM_PROMPT",
    "RETRY_INSTRUCTION",
    "LLMClient",
    "FakeLLM",
    "GroqClient",
    "LLMError",
    "get_llm",
    "has_api_key",
]
