"""HDFC Mutual Fund FAQ Assistant - single-page UI (PRD 7, 8.1).

Facts-only. No advice. Every answer is validated before it is shown; the
"How this was answered" expander exposes the trace for all six stages.

No chat history is persisted to disk, no cookies, no analytics (PRD 7).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Preflight before importing anything heavy. A `streamlit` on PATH that is not
# the venv's will start the server fine and then die on the first missing import,
# which looks like an app bug. Say what is actually wrong instead.
_MISSING = [
    name
    for name in ("pydantic", "yaml", "chromadb", "sentence_transformers", "dotenv")
    if importlib.util.find_spec(name) is None
]
if _MISSING:
    import streamlit as _st

    _st.set_page_config(page_title="HDFC Mutual Fund FAQ Assistant", page_icon="📊")
    _st.error(
        "This app is running with the wrong Python. Missing: "
        + ", ".join(_MISSING)
    )
    _st.code(
        "cd "
        + str(Path(__file__).resolve().parents[1])
        + "\n"
        + ".venv/bin/pip install -r requirements.txt\n"
        + ".venv/bin/streamlit run app/streamlit_app.py",
        language="bash",
    )
    _st.stop()

import streamlit as st

# PRD 8.1, verbatim, unmodified.
DISCLAIMER = (
    "**Facts-only. No investment advice.** This assistant answers factual "
    "questions about 5 HDFC Mutual Fund schemes using public Groww pages. It "
    "does not recommend buying, selling, or holding any fund, and it does not "
    "compute or compare returns. Mutual fund investments are subject to market "
    "risks; read all scheme-related documents carefully. Verify every figure on "
    "the linked source page before acting."
)

EXAMPLE_QUESTIONS = [
    "What is the expense ratio of HDFC Small Cap?",
    "Is there an exit load on HDFC ELSS Tax Saver?",
    "What is the minimum SIP for HDFC Flexi Cap?",
]

# architecture.md 10: Streamlit state is in-memory per session - no DB, no
# cookies, no analytics, nothing written to disk. The window is a sliding
# deque, so turn 11 evicts turn 1.
MAX_HISTORY_TURNS = 10

INGEST_HINT = "Collection is empty. Run:\n\n    python scripts/ingest.py"


def history() -> list:
    if "history" not in st.session_state:
        st.session_state["history"] = []
    return st.session_state["history"]


def remember(question: str, result) -> None:
    """Append a turn, honouring the no-PII-storage rule.

    architecture.md 594: PII-bearing queries are never written to logs, Chroma,
    or session_state. A PII refusal therefore contributes no stored question
    text - only a redaction marker.
    """
    if result.refused and result.refusal_type == "pii":
        st.session_state["history"].append(
            {"question": "[redacted: personal identifier]", "answer": result.text}
        )
    else:
        st.session_state["history"].append(
            {"question": question, "answer": result.text}
        )
    if len(st.session_state["history"]) > MAX_HISTORY_TURNS:
        st.session_state["history"] = st.session_state["history"][-MAX_HISTORY_TURNS:]


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")


_load_dotenv()

st.set_page_config(page_title="HDFC Mutual Fund FAQ Assistant", page_icon="📊", layout="centered")


@st.cache_resource(show_spinner=False)
def _boot():
    """Load config and the LLM once. Returns (config, llm, error)."""
    from src.config import load_config
    from src.llm.client import LLMError, get_llm

    config = load_config()
    try:
        llm = get_llm(config)
    except LLMError as exc:
        return config, None, str(exc)
    return config, llm, None


def _collection_ready(config) -> bool:
    from src.pipeline.stage4_store import get_client, get_or_create_collection

    collection = get_or_create_collection(get_client(config), config)
    return collection.count() > 0


config, llm, boot_error = _boot()

st.title("HDFC Mutual Fund FAQ Assistant")
st.caption("Facts-only. No investment advice.")

with st.sidebar:
    st.markdown(DISCLAIMER)
    st.divider()
    st.caption("Source pages: 5 public Groww scheme pages. Nothing else is consulted.")
    if boot_error:
        st.warning(boot_error)
    elif os.environ.get("LLM_PROVIDER", "fake") == "fake":
        st.info(
            "Running with `LLM_PROVIDER=fake` (canned answers). "
            "Set `LLM_PROVIDER=groq` in `.env` for live answers."
        )
    st.divider()
    st.caption(f"Memory: last {MAX_HISTORY_TURNS} questions, this session only.")
    if st.button("Clear conversation", use_container_width=True):
        st.session_state["history"] = []
        st.rerun()

if not _collection_ready(config):
    st.error(INGEST_HINT)
    st.stop()

if llm is None:
    st.error(
        "No LLM configured. Copy `.env.example` to `.env`, add a key, or set "
        "`LLM_PROVIDER=fake` to run against canned answers."
    )
    st.stop()

st.subheader("Try:")
query = None
columns = st.columns(3)
for column, question in zip(columns, EXAMPLE_QUESTIONS):
    if column.button(question, key=f"example_{question}"):
        query = question

typed = st.text_input(
    "Your question",
    key="question_box",
    placeholder="Ask a factual question...",
    label_visibility="collapsed",
)
if typed:
    query = typed

if not query:
    st.stop()


def _render(result) -> None:
    if result.refused:
        st.warning(result.text)
        st.caption(f"Refused: {result.refusal_type}")
        return

    st.markdown(result.text)

    if result.citation_url:
        st.markdown(f"Source: [{result.citation_url}]({result.citation_url})")
    if result.last_updated:
        st.caption(f"Last updated from sources: {result.last_updated}")

    if result.validation and not result.validation.passed:
        with st.expander("Validation notes", expanded=False):
            st.json(
                {
                    "passed": result.validation.passed,
                    "failures": result.validation.failures,
                    "checks": result.validation.checks,
                    "details": result.validation.details,
                }
            )

    with st.expander("How this was answered"):
        from src.observability.trace import trace_to_json

        st.code(trace_to_json(result.trace), language="json")


with st.spinner("Looking this up..."):
    from src.pipeline.stage6_generate import answer

    # Only non-PII turns are passed forward as context.
    context_history = [
        turn
        for turn in history()
        if not turn["question"].startswith("[redacted")
    ][-MAX_HISTORY_TURNS:]
    try:
        result = answer(query, config, llm, history=context_history)
    except Exception as exc:  # never a bare traceback in the UI
        st.error(f"Something went wrong answering that: {type(exc).__name__}")
        st.caption(str(exc)[:300])
        st.stop()

remember(query, result)
_render(result)

with st.expander(
    f"Conversation ({len(history())} of last {MAX_HISTORY_TURNS})", expanded=False
):
    if not history():
        st.caption("No questions yet.")
    for turn in history():
        st.markdown(f"**Q:** {turn['question']}")
        st.caption(turn["answer"][:220])
    st.caption(
        "In-memory only for this session. Nothing is written to disk, and "
        "personal identifiers are redacted before storage."
    )
