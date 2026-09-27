# ARCHITECTURE — HDFC Mutual Fund FAQ Assistant

| | |
|---|---|
| **Project** | Groww MF-Guide — class project |
| **Document** | Technical architecture, derived from `PRD.md` v1.0 |
| **Owner** | Surya Harshini Gundreddy |
| **Status** | Draft v1.0 |
| **Date** | 27 Sep 2026 |
| **Audience** | Build team + evaluator. Read alongside `PRD.md`. |

---

## 1. Purpose

`PRD.md` defines *what* the assistant must do. This document defines *how* it is built: module boundaries, data contracts between the six pipeline stages, the chunking strategy selector, guard placement, and the test matrix.

**Read order:** PRD §5 (pipeline) → this §4 (module map) → §7 (stage contracts) → §15 (traceability).

### 1.1 Requirement vocabulary

Short forms used throughout, all defined in `PRD.md`:

| Short form | Meaning | PRD ref |
|---|---|---|
| **Stage 1–6** | Loading, Chunking, Embedding, Store, Retrieve+Guard, Generate | §5 |
| **R1–R4** | Chunking-strategy selection rules | §6.2 |
| **Allowlist** | The 5 permitted source URLs; enforced at fetch *and* citation | §3, S4 |
| **Gold fact** | A `label: value` pair the corpus is expected to yield | §3.1 |
| **Grounding check** | Answer's key value must appear verbatim in a retrieved chunk | §6.4 |
| **Trace** | Per-answer record of all 6 stages, surfaced in the UI | §10 |

---

## 2. Architecture Principles

| # | Principle | Consequence in code |
|---|---|---|
| P1 | **Allowlist is the trust boundary** | No code path can fetch or cite a URL outside `config.yaml: sources.allowlist` |
| P2 | **Guards run before the vector store, not after** | PII never reaches Chroma, the LLM, or any log |
| P3 | **Extract, don't scrape** | The parser targets *known* labelled fields (§7.2.2), not arbitrary page text. Parse failure degrades loudly |
| P4 | **Ingestion is idempotent** | Chunk ids are deterministic (`{scheme_id}_{field}_{n}`), so re-running upserts instead of duplicating |
| P5 | **Ungrounded is a valid answer** | "Not in my corpus" is a first-class outcome, not an error |
| P6 | **Every answer is reconstructable** | A `Trace` is built as the answer is generated, never retrofitted |
| P7 | **Strategy decisions are data, not code paths** | Chunking strategy is the output of a selector returning a `StrategyDecision` object (§7.2.1) |
| P8 | **Snapshots by default, live on request** | Demo runs from `data/raw/`; `--live` re-fetches. PRD §15 Q2 |

---

## 3. System Context

```
        ┌────────────────────────────────────────────────────────────┐
        │                  User (browser)                            │
        │            "expense ratio of HDFC ELSS?"                   │
        └───────────────────────────┬────────────────────────────────┘
                                    │ HTTPS
        ┌───────────────────────────▼────────────────────────────────┐
        │              Streamlit UI  (app/streamlit_app.py)           │
        │   welcome line · 3 example questions · disclaimer           │
        │   answer + 1 citation + "Last updated from sources: …"      │
        │   collapsible "How this was answered" → Trace              │
        └───────────────────────────┬────────────────────────────────┘
                                    │ in-process call
        ┌───────────────────────────▼────────────────────────────────┐
        │                    AnswerService                          │
        │  guard → resolve → retrieve → generate → validate          │
        └──┬──────────────┬───────────────┬──────────────┬──────────┘
           │              │               │              │
     ┌─────▼─────┐  ┌─────▼──────┐  ┌─────▼─────┐  ┌─────▼──────┐
     │  Guards   │  │  ChromaDB  │  │  LLM      │  │  Trace     │
     │  (regex + │  │  Persistent│  │  adapter  │  │  builder   │
     │  classif.)│  │  ./chroma  │  │  temp=0   │  │  (in-mem)  │
     └───────────┘  └─────▲──────┘  └───────────┘  └────────────┘
                          │ vectors written offline by `ingest.py`
        ┌─────────────────┴──────────────────────────────────────────┐
        │                  OFFLINE PIPELINE  (ingest.py)            │
        │  Stage1 Load → Stage2 Chunk → Stage3 Embed → Stage4 Store │
        └────────────────────────────┬───────────────────────────────┘
                                     │ reads allowlisted pages
                             ┌───────▼────────┐
                             │ data/raw/*.txt │  (snapshot or --live)
                             └───────┬────────┘
                                     │
                     ┌───────────────▼───────────────┐
                     │  5 public Groww scheme pages  │  ← only allowed input
                     └───────────────────────────────┘
```

**Runtime split:** the pipeline runs **offline** (once, or on demand); the assistant runs **online** per question. The two share only the Chroma collection and the allowlist. This keeps the demo fast and reproducible, and means ingestion bugs can never cause a bad answer to be silently generated.

---

## 4. Repository & Module Map

```
GrowApp/
├── PRD.md
├── architecture.md
├── README.md
├── requirements.txt
├── config.yaml
│
├── data/
│   ├── raw/                       Stage 1 output snapshot (committed)
│   │   ├── hdfc_large_cap.txt
│   │   ├── hdfc_flexi_cap.txt
│   │   ├── hdfc_elss.txt
│   │   ├── hdfc_small_cap.txt
│   │   └── hdfc_balanced_advantage.txt
│   ├── processed/
│   │   ├── documents.jsonl        normalised text + fetched_at
│   │   └── chunks.jsonl           every chunk, human-readable
│   └── chunks.embeddings.npz      optional cache
│
├── chroma_db/                     PersistentClient directory
├── logs/
│   ├── ingest_run.json            strategy verdict, params, counts
│   └── eval_results.json          golden + refusal results
│
├── src/
│   ├── config.py                  typed config loader
│   ├── models.py                  all dataclasses / pydantic schemas
│   ├── pipeline/
│   │   ├── stage1_load.py
│   │   ├── stage2_chunk.py
│   │   ├── stage3_embed.py
│   │   ├── stage4_store.py
│   │   ├── stage5_retrieve.py
│   │   └── stage6_generate.py
│   ├── guards/
│   │   ├── pii.py                 S1
│   │   ├── intent.py              S2 (advice / out-of-corpus)
│   │   ├── performance.py         S3
│   │   └── validator.py           §6.4 post-validator
│   ├── retrieval/
│   │   ├── aliases.py             scheme resolution + `equity fund → flexi cap`
│   │   ├── embedder.py            MiniLM singleton, prefix template
│   │   └── rerank.py              MMR (λ=0.3)
│   ├── llm/
│   │   └── client.py              provider-agnostic adapter
│   └── observability/
│       ├── trace.py               Trace builder
│       └── runlog.py              ingest run log writer
│
├── scripts/
│   ├── ingest.py                  orchestrates Stages 1–4
│   ├── evaluate.py                golden / refusal / PII suites
│   └── seed_snapshot.py           one-off live fetch → data/raw/
│
├── app/
│   └── streamlit_app.py
│
├── eval/
│   ├── golden_qa.json             10 queries + expected values (PRD §11)
│   ├── refusal_qa.json            5 advice/performance queries
│   └── pii_cases.json             5 PII strings, never persisted
│
└── tests/
    ├── test_stage1_load.py
    ├── test_stage2_chunk.py
    ├── test_stage3_embed.py
    ├── test_stage4_store.py
    ├── test_guards.py
    ├── test_aliases.py
    ├── test_validator.py
    └── test_pipeline_integration.py
```

**Layering rule:** `pipeline/*` may import `models`, `config`, `observability`, `retrieval/embedder`. It may **not** import `app/` or `guards/*` (guards are runtime-only, PRD §6.4). `guards/*` and `retrieval/*` may not import each other.

---

## 5. Core Data Models

All models are pydantic. Field names below are the contract between stages; changing one is a breaking change.

### 5.1 `Document` — output of Stage 1

| Field | Type | Notes |
|---|---|---|
| `doc_id` | `str` | `{scheme_id}` |
| `scheme_id` | `str` | canonical slug, e.g. `hdfc_elss` |
| `scheme_name` | `str` | exact name as printed on the page |
| `plan` | `Literal["Direct","Growth"]` | both fixed for this corpus |
| `category` | `str` | e.g. `Equity ELSS` |
| `source_url` | `str` | must be in allowlist |
| `fetched_at` | `date` | ISO; printed in every answer (G5) |
| `text` | `str` | normalised main-content text |
| `text_sha256` | `str` | snapshot integrity + idempotency check |

### 5.2 `Chunk` — output of Stage 2, unit of embedding

| Field | Type | Notes |
|---|---|---|
| `chunk_id` | `str` | `{scheme_id}_{field}_{n}`, deterministic (P4) |
| `doc_id` | `str` | FK → `Document.doc_id` |
| `scheme_id` | `str` | **the critical filter key** (§7.5.3) |
| `section` | `str` | `overview` · `fees_and_minimums` · `portfolio` · `fund_management` · `about` |
| `field` | `str` | canonical gold-fact name (§7.2.2) |
| `value` | `str \| None` | extracted value, verbatim |
| `text` | `str` | the chunk body sent for embedding |
| `embed_input` | `str` | `"{scheme_name} ({category}). {text}"` — what actually gets embedded |
| `source_url` | `str` | copied from `Document`; never inferred |
| `effective_date` | `date \| None` | for dated exit-load rows |
| `historical` | `bool` | `True` for superseded exit-load rows (PRD §3.2 #4) |
| `is_fact` | `bool` | `True` if `field` is a known gold fact |
| `token_count` | `int` | measured, asserted ≤ 234 (§7.4.2) |

### 5.3 Runtime models

| Model | Purpose | Key fields |
|---|---|---|
| `GuardDecision` | One guard's verdict | `guard`, `triggered`, `reason`, `refusal_text`, `loggable` (P2) |
| `SchemeResolution` | Resolved query target | `scheme_ids[]`, `matched_alias`, `confidence`, `ambiguous` |
| `RetrievedChunk` | A search hit | `chunk`, `cosine_distance`, `rank` |
| `ValidationResult` | Post-validator output | `passed`, `checks{}`, `failures[]` |
| `Answer` | Final response | `text`, `citation_url`, `last_updated`, `refused`, `refusal_type`, `validation`, `trace` |
| `Trace` | §10 panel payload | `stage1..stage6` sub-objects |

---

## 6. Configuration

Single source of truth in `config.yaml`. Nothing hardcoded in modules.

```yaml
project:
  name: Groww MF-Guide
  amc: HDFC Mutual Fund
  mode: snapshot

sources:
  allowlist:
    - scheme_id: hdfc_large_cap
      category: Large Cap
      scheme_name: HDFC Large Cap Fund Direct Growth
      url: https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth
    - scheme_id: hdfc_flexi_cap
      category: Flexi Cap
      scheme_name: HDFC Flexi Cap Direct Plan Growth
      url: https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth
    - scheme_id: hdfc_elss
      category: ELSS
      scheme_name: HDFC ELSS Tax Saver Fund Direct Plan Growth
      url: https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth
    - scheme_id: hdfc_small_cap
      category: Small Cap
      scheme_name: HDFC Small Cap Fund Direct Growth
      url: https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth
    - scheme_id: hdfc_balanced_advantage
      category: Balanced Advantage
      scheme_name: HDFC Balanced Advantage Fund Direct Growth
      url: https://groww.in/mutual-funds/hdfc-balanced-advantage-fund-direct-growth
  user_agent: "GrowwMFGuide/0.1 (class project; contact: repo README)"
  timeout_s: 20
  max_retries: 3

load:
  drop_selectors:
    - nav
    - header
    - footer
    - aside
    - "[data-testid*='compare']"
    - "[class*='return-calculator']"
    - "[class*='understand-terms']"
  min_text_chars: 1500

chunk:
  strategy: auto
  semantic_available: true
  size_tokens: 200
  overlap_tokens: 40
  separators: ["\n\n", "\n", ". ", " ", ""]
  never_split: [label_value_pair, holdings_row]
  holdings_mode: sector_summary

embed:
  model_id: sentence-transformers/all-MiniLM-L6-v2
  dim: 384
  max_seq_length: 256
  prefix_template: "{scheme_name} ({category}). {text}"
  normalize: true
  batch_size: 32
  assert_no_truncation: true

store:
  backend: chromadb
  path: ./chroma_db
  collection: hdfc_mf_faq
  metric: cosine

retrieve:
  top_k: 6
  mmr_lambda: 0.3
  oversample_factor: 3
  sim_floor: 0.30

generate:
  max_sentences: 3
  temperature: 0.0
  max_tokens: 220
  citations: exactly_one
  retry_on_validation_failure: 1
  require_scheme_and_plan_in_answer: true

guards:
  pii_patterns_enabled: true
  redact_pii_from_logs: true
  max_query_chars: 500
```

---

## 7. Stage Designs

### 7.1 Stage 1 — Loading

**Contract in:** allowlisted URL. **Contract out:** `data/raw/{scheme_id}.txt` + `Document[]`.

```
allowlist check ─► HTTP fetch ─► main-content extract ─► noise strip
      │                                                        │
      │(fail = hard)                                           ▼
      └──────────────────────────────► normalise ─► text_sha256 ─► persist
```

| Step | Behaviour | Failure mode |
|---|---|---|
| Allowlist check | Normalise URL (scheme, host, strip query/fragment, trailing slash) and assert membership | **Hard fail.** `AllowlistViolation`. No fallback fetch |
| HTTP fetch | `requests.Session`, explicit UA, 20 s timeout, 3 retries with exponential backoff | Retry exhausted → `FetchError`, ingest aborts. Never ingest a partial corpus |
| Main-content extract | `trafilatura` with explicit `include_tables=False`, falling back to `<main>`/largest `<div>` | Below `min_text_chars` → `ExtractionError` (loud, per P3) |
| Noise strip | Remove the selectors in `load.drop_selectors`, then remove the 4 labelled noise blocks by regex: mega-menu, ticker sidebar, `Compare similar funds` table, SIP return calculator, `Understand terms` glossary, `See All` holdings overflow | Noisy leftovers are caught by the Stage 2 audit (§7.2.4) |
| Normalise | Collapse whitespace, unify dashes, **preserve `₹`, `%`, `Cr`, parentheses** | Never strip currency symbols — they are the fact values |
| Snapshot | Write `data/raw/{scheme_id}.txt`; record `fetched_at` | — |

**Why `include_tables=False`:** the holdings tables are 300+ rows of noise that would dominate the corpus. Top-holding facts are extracted deliberately in Stage 2 (§7.2.2) rather than swallowed whole.

**Verification gate:** `len(Document) == 5` and each `text` contains its own scheme name and its gold facts (expense ratio, min SIP, benchmark). Asserted in `ingest.py` before Stage 2 begins.

### 7.2 Stage 2 — Chunking

**Contract in:** `Document[]`. **Contract out:** `Chunk[]` + `StrategyDecision` written to `logs/ingest_run.json`.

#### 7.2.1 Strategy selector (P7, PRD §6.2)

`select_chunking_strategy(corpus_stats) -> StrategyDecision` evaluates R1–R4 and records the verdict. It is a pure function of corpus statistics, so the run log explains the choice without re-running it.

```python
@dataclass
class StrategyDecision:
    selected: str
    rejected: list[str]
    rules: dict[str, bool]
    rationale: str
    params: ChunkParams
```

| Rule | Implementation | Expected verdict on our corpus |
|---|---|---|
| R1 labelled facts | fraction of lines matching a `label: value` gold-fact pattern > 0.15 | `True` |
| R2 small corpus | `doc_count <= 10 and mean_doc_chars <= 60000` | `True` |
| R3 explicit topics | `True` when R1 holds (labels already delimit topics) | `True` |
| R4 single-fact queries | `True` when ≥ 80% of eval queries are single-fact | `True` |

**Decision:** `label_aware_recursive` selected. `semantic` rejected — R2 and R3 fail: the corpus is 5 short pages, and topics are already explicit, so an embedding pass to propose boundaries adds cost and non-determinism while grouping unrelated labels (NAV beside exit load) and degrading single-fact precision. `semantic` stays reachable via `chunk.strategy: semantic` for the class comparison demo (FR15).

#### 7.2.2 Section parsing and the label registry (P3)

This is where retrieval precision is won. Rather than splitting free text, we parse the page's own labelled fields.

| Section | Extraction | `field` values produced |
|---|---|---|
| `overview` | Header metric strip | `nav`, `fund_aum`, `expense_ratio`, `min_sip`, `rating`, `category`, `risk_rating`, `holdings_count` |
| `fees_and_minimums` | `Minimum investments` + `Exit load, stamp duty and tax` blocks | `min_first_inv`, `min_second_inv`, `exit_load`, `exit_load_historical`, `stamp_duty`, `tax_note` |
| `portfolio` | Holdings table (rows off, sector summary on) | `top_holding`, `sector_allocation` |
| `fund_management` | `Compare Fund management` bios | `fund_manager` |
| `about` | `About …` block, `Investment Objective`, `Fund benchmark`, `Date of Incorporation`, `Custodian`, `Registrar & Transfer Agent`, `Total AUM` | `benchmark`, `investment_objective`, `launch_date`, `custodian`, `rta`, `fund_house`, `amc_aum_total` |
| `elss` | Badge text `ELSS • 3Y Lock-in` | `lock_in` |

**Two registry annotations are load-bearing:**

- `amc_aum_total` carries `scope: amc` in its chunk text prefix. The About block prints `Total AUM ₹9,86,236.84 Cr` — **identical on all 5 pages** — so it is the single highest misattribution risk in the corpus (PRD §3.2 #3). Fund-level AUM comes only from `fund_aum`.
- `exit_load_historical` chunks set `historical: true` and `effective_date` from the row's date label. Small Cap has four such rows; only the latest is current.

**Never-split rules:** a `label: value` pair is atomic; a holdings row is atomic; a fund-manager bio paragraph is split only at sentence boundaries, never mid-name.

**Worked example — one source page → 3 chunks:**

```json
{"chunk_id":"hdfc_elss_fees_min_sip_1","scheme_id":"hdfc_elss",
 "section":"fees_and_minimums","field":"min_sip","value":"₹500","is_fact":true,
 "text":"HDFC ELSS Tax Saver Fund Direct Plan Growth — Minimum investments. Min. for 1st investment: ₹500. Min. for 2nd investment: ₹500. Min. for SIP: ₹500.",
 "embed_input":"HDFC ELSS Tax Saver Fund Direct Plan Growth (Equity ELSS). HDFC ELSS Tax Saver Fund Direct Plan Growth — Minimum investments. Min. for 1st investment: ₹500. Min. for 2nd investment: ₹500. Min. for SIP: ₹500.",
 "source_url":"https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
 "effective_date":null,"historical":false,"token_count":71}

{"chunk_id":"hdfc_elss_fees_exit_load_1","scheme_id":"hdfc_elss",
 "section":"fees_and_minimums","field":"exit_load","value":"Nil","is_fact":true,
 "text":"HDFC ELSS Tax Saver Fund Direct Plan Growth — Exit load: Nil. No exit load is charged on redemption. ELSS: 3Y lock-in. Stamp duty on investment: 0.005% (from July 1st, 2020).",
 "embed_input":"HDFC ELSS Tax Saver Fund Direct Plan Growth (Equity ELSS). HDFC ELSS Tax Saver Fund Direct Plan Growth — Exit load: Nil. No exit load is charged on redemption. ELSS: 3Y lock-in. Stamp duty on investment: 0.005% (from July 1st, 2020).",
 "source_url":"https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
 "effective_date":"2026-09-27","historical":false,"token_count":74}

{"chunk_id":"hdfc_elss_fees_exit_load_hist_1","scheme_id":"hdfc_elss",
 "section":"fees_and_minimums","field":"exit_load_historical","value":null,"is_fact":false,
 "text":"HDFC ELSS Tax Saver Fund Direct Plan Growth — Exit load, historical row effective 01 Jan 2013: no exit load recorded.",
 "embed_input":"HDFC ELSS Tax Saver Fund Direct Plan Growth (Equity ELSS). HDFC ELSS Tax Saver Fund Direct Plan Growth — Exit load, historical row effective 01 Jan 2013: no exit load recorded.",
 "source_url":"https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth",
 "effective_date":"2013-01-01","historical":true,"token_count":52}
```

Note the `embed_input` duplication of the scheme name. The raw `text` is padded so the **retrieved** chunk is self-describing in the UI, while `embed_input` — the version actually vectorised — carries the category too, making short chunks scheme-discriminative (PRD §6.3). This redundancy is intentional: 200-token budget absorbs it, and it is what stops "Min. for SIP ₹500" from matching all five schemes.

**Expected yield:** ~10 gold-fact chunks + 2–5 historical + ~2 prose chunks per page → **~70–90 chunks total** from 5 pages.

#### 7.2.3 Fallback splitter

Prose blocks that yield no recognised label go to `RecursiveCharacterTextSplitter.from_tiktoken_encoder(encoding_name="cl100k_base")` with `chunk_size=200`, `chunk_overlap=40`, and the `separators` from config. Using a different tokenizer here is acceptable because §7.4.2 re-measures with the *embedding* tokenizer before writing.

#### 7.2.4 Ingest audit (Stage 2 self-check)

`ingest.py` aborts if any of these fail:

- every gold fact in PRD §3.1 that should be extractable **is** extractable, with the expected value;
- no chunk is `> 200` tokens;
- no chunk contains a stripped-noise marker (`Compare similar funds`, `Return calculator`, `Understand terms`);
- no `label: value` pair is split across chunks;
- every chunk's `source_url` is in the allowlist;
- the 5 schemes produce distinct `fund_aum`, `expense_ratio`, `benchmark` values — catching a parser that cross-contaminated schemes.

### 7.3 Stage 3 — Embedding

**Contract in:** `Chunk[]`. **Contract out:** `float32[N, 384]`, written to Chroma by Stage 4.

| Decision | Value | Reason |
|---|---|---|
| Model | `sentence-transformers/all-MiniLM-L6-v2` | PRD §5.1. 384-dim, ~80 MB, CPU-viable |
| Input | `embed_input`, **not** `text` | Prefix makes chunks scheme-discriminative |
| Pooling | mean pooling (model default) | Matches the model's training regime |
| Normalisation | L2, `normalize_embeddings=True` | Makes cosine distance valid (unit vectors) |
| Truncation | `truncate_dim` untouched; hard assert instead | Silent truncation is the failure mode in PRD §14 |

#### 7.3.1 No-truncation assertion (fixes PRD §14 row 4)

```python
MAX_SEQ = 256
SPECIAL = 2
PREFIX_RESERVE = 24
BUDGET = MAX_SEQ - SPECIAL - PREFIX_RESERVE

def assert_no_truncation(chunks, tokenizer):
    offenders = [c.chunk_id for c in chunks
                 if len(tokenizer(c.embed_input)["input_ids"]) > BUDGET]
    if offenders:
        raise IngestError(f"token budget exceeded: {offenders}")
```

`BUDGET = 230`, against a `size_tokens: 200` target — ~30 tokens of headroom. This turns a **silent correctness bug** (a fact cut in half, then confidently answered wrong) into a **loud ingest failure**.

#### 7.3.2 Caching

`chunks.embeddings.npz` keyed by `text_sha256 + model_id`. Re-ingest with an unchanged corpus loads the cache and skips the model entirely, making M7 iteration fast.

### 7.4 Stage 4 — Store vector data

**Contract in:** `Chunk[]` + embeddings. **Contract out:** queryable collection.

| Setting | Value | Reason |
|---|---|---|
| Client | `chromadb.PersistentClient(path="./chroma_db")` | Survives restarts; no server |
| Collection | `hdfc_mf_faq` | — |
| Distance | `hnsw:space=cosine` | Vectors are L2-normalised, so cosine is correct and cheap |
| IDs | `chunk.chunk_id` | Deterministic → upsert, no duplicates (P4) |
| Metadata | `scheme_id`, `section`, `field`, `value`, `source_url`, `effective_date`, `historical`, `is_fact`, `fetched_at` | Every filter in Stage 5 comes from here |
| Documents | `chunk.text` | Human-readable in the UI panel |

`scheme_id` as first-class metadata is the single most important decision in the whole store: it makes scheme scoping a **filter**, not a ranking hope (§7.5.3).

### 7.5 Stage 5 — Retrieve + Guard

**Contract in:** user query. **Contract out:** `RetrievedChunk[]` for the prompt, or an early refusal.

#### 7.5.1 Order of operations (P2 — guards before the store)

| # | Step | On trigger |
|---|---|---|
| 1 | Normalise: strip, collapse whitespace, truncate to `max_query_chars` | — |
| 2 | **PII guard** (`S1`) | Immediate refusal. Query **not** logged, not embedded, not sent to LLM |
| 3 | **Advice-intent guard** (`S2`) | Refusal + educational link |
| 4 | **Performance guard** (`S3`) | Decline; link official factsheet |
| 5 | **Out-of-corpus guard** | List the 5 in-scope schemes |
| 6 | Scheme resolution (alias table + fuzzy) | Unresolved & cross-scheme → no filter, wider retrieval |
| 7 | Vector search, `n_results = top_k * oversample_factor = 18`, `where={"scheme_id": …}` if resolved | — |
| 8 | MMR rerank, λ=0.3 → top 4 | — |
| 9 | `sim_floor` gate: `1 - min(cosine_distance) < 0.30` → "not in my corpus" (P5) | Graceful unanswer |
| 10 | Build prompt from chunks | — |

#### 7.5.2 Guard implementations

**PII (`S1`) — regex, pre-retrieval, non-persisting.** Detects PAN (`[A-Z]{5}[0-9]{4}[A-Z]`), Aadhaar (12 digits, with optional whitespace/label), folio/account numbers, 4–6 digit OTPs, emails, and phone numbers. The refusal is a fixed string; the matched text is **discarded in memory and never written anywhere** (`guards.pii.redact_pii_from_logs: true`).

**Advice intent (`S2`) — two-layer.** Layer 1 is a keyword/phrase lexicon (`should i buy`, `which is better`, `is it a good time`, `recommend`, `suggest`, `worth it`, `best fund for me`, `build me a portfolio`). Layer 2 is a zero-shot classifier over the LLM adapter for paraphrases the lexicon misses. Layer 2 alone would be slower and non-deterministic; layer 1 alone would leak. Both must agree to block.

**Performance (`S3`)** triggers on `will it return`, `expected return`, `projected`, `which performed better`, `compare returns`, `vs` + return words, `X% return`. Response declines to compute or compare and links the official factsheet.

**Out-of-corpus** uses the alias table: if a recognised AMC/brand appears that is not HDFC, or a scheme resolves to none of the 5, the bot states its scope.

#### 7.5.3 Scheme resolution and the `equity fund → flexi cap` trap

`hdfc-equity-fund-direct-growth` is the **Flexi Cap** scheme. `retrieval/aliases.py` holds an explicit table, not heuristics:

| Alias / token pattern | Resolves to |
|---|---|
| `large cap`, `hdfc large cap fund` | `hdfc_large_cap` |
| `flexi cap`, `equity fund`, `hdfc equity fund` | `hdfc_flexi_cap` |
| `elss`, `tax saver`, `80c`, `80c tax saver` | `hdfc_elss` |
| `small cap` | `hdfc_small_cap` |
| `balanced advantage`, `balanced` | `hdfc_balanced_advantage` |

Unmatched scheme terms → `scheme_ids=[]`, `ambiguous=True`; retrieval runs unfiltered and the prompt is told to state which scheme each fact belongs to. This is the PRD §3.2 #2 control.

#### 7.5.4 MMR rerank

Chroma's Python client has no first-class MMR across all versions, so it is implemented explicitly in `retrieval/rerank.py`: take the 18 candidates, greedily maximise

```
score = (1 - λ) · (1 - d(q, c_i)) - λ · max_{j in selected} d(c_i, c_j)
```

with λ = 0.3, keeping the top 4. Purpose: stop three near-identical `min_sip` chunks from one page crowding out the one chunk that actually answers the question.

### 7.6 Stage 6 — Generate + Validate

**Contract in:** query + top chunks. **Contract out:** `Answer` with a `ValidationResult`.

Prompt contract (PRD §6.5) is reproduced verbatim in `llm/client.py` as a module constant, with the AUM disambiguation rule and the historical-exit-load rule stated explicitly.

#### 7.6.1 Post-validator — the load-bearing gate

`guards/validator.py`, applied to every generated answer:

| Check | Test | On failure |
|---|---|---|
| `citation_present` | exactly one URL in the answer | retry |
| `citation_allowed` | that URL ∈ allowlist | **discard answer**, retry |
| `grounded` | every numeric/currency/date token in the answer appears **verbatim** in some retrieved chunk | retry |
| `scheme_named` | the resolved scheme name or a distinguishing alias is present | retry |
| `no_advice` | advice lexicon absent | **discard answer**, no retry |
| `no_pii` | no PII pattern in the answer | **discard answer**, no retry |
| `sentence_budget` | ≤ 3 sentences | retry |
| `has_last_updated` | `Last updated from sources:` line present | retry |

Retry budget: 1 (`generate.retry_on_validation_failure: 1`), with stricter instructions appended. After that, return the safe fallback — *"I found the page but can't give a verified one-line answer. Please check the source directly."* — plus the link. **Never return an unvalidated answer** (S6).

The `grounded` check is what catches wrong-scheme attribution, and it is verified by fault injection in §9: hand the generator a chunk from a *different* scheme and confirm rejection.

---

## 8. Cross-Cutting Concerns

### 8.1 Observability (§10)

`observability/trace.py` builds a `Trace` incrementally, one sub-object per stage, emitted as JSON in the UI's "How this was answered" panel:

| Stage | Traced |
|---|---|
| 1 | `source_url`, `fetched_at`, `text_sha256`, chars |
| 2 | `chunk_id`s, `token_count`s, `StrategyDecision` (rules R1–R4 + params) |
| 3 | `model_id`, `dim`, `truncation_assert_passed`, cache hit/miss |
| 4 | collection name, `n_chunks`, distance metric |
| 5 | `GuardDecision[]`, `SchemeResolution`, top-4 `(chunk_id, cosine)` |
| 6 | final text, `citation_url`, `ValidationResult`, retry count |

`logs/ingest_run.json` captures the full ingest: strategy verdict, params, chunk counts per scheme, audit results, duration. This is the artifact that demonstrates Stage 1–4 actually ran.

### 8.2 Error handling

| Failure | Handling | User sees |
|---|---|---|
| Site layout changed → extraction fails | Ingest aborts loudly (P3). Snapshot remains usable | n/a — demo uses snapshot |
| Chroma empty / missing | `RuntimeError` at startup with the fix command | App-level error: "Run `python scripts/ingest.py`" |
| LLM timeout / provider error | One retry, then safe fallback + link | Fallback text |
| `sim_floor` gate trips | No LLM call at all | "Not in my corpus" + link |
| Validator fails twice | No unvalidated output (S6) | Safe fallback + link |

No stage ever returns partial data silently.

### 8.3 Security & privacy (S1–S8)

| Control | Implementation |
|---|---|
| No PII storage | Guards run before retrieval; PII-bearing queries are never written to logs, Chroma, or Streamlit `session_state` |
| No persistence of session | Streamlit state is in-memory per session; no DB, no cookies, no analytics |
| No secret leakage | LLM key from environment only; `.env` gitignored; never echoed to the Trace |
| Allowlist enforcement | One module owns URL validation; both fetch and citation paths call it |
| No vendor back-end leakage | UI renders only our own widgets and our own extracted text |

---

## 9. Testing Strategy

| Suite | File | Covers |
|---|---|---|
| Loader | `test_stage1_load.py` | 5 docs, noise stripped, `min_text_chars`, snapshot hashes stable, allowlist rejects a 6th URL |
| Chunker | `test_stage2_chunk.py` | All gold facts from PRD §3.1 extracted with exact values; no `label: value` split; `historical` flags correct; no chunk > 200 tokens; 5 schemes yield distinct `fund_aum` |
| Strategy selector | `test_stage2_chunk.py` | R1–R4 verdicts on a synthetic corpus; `label_aware_recursive` chosen; `semantic` reachable by flag |
| Embedder | `test_stage3_embed.py` | dim = 384; vectors unit-norm; no-truncation assert fires on an oversized chunk |
| Store | `test_stage4_store.py` | Idempotent re-ingest keeps count constant; `scheme_id` filter returns only that scheme |
| Guards | `test_guards.py` | 5/5 PII blocked and unlogged; 5/5 advice refused; 5/5 performance declined |
| Aliases | `test_aliases.py` | `equity fund → hdfc_flexi_cap`; unknown scheme → `ambiguous` |
| Validator | `test_validator.py` | Each of the 8 checks fires independently |
| Integration | `test_pipeline_integration.py` | End-to-end: ingest → query → validated `Answer` with an allowlisted citation |
| Eval | `scripts/evaluate.py` | PRD §11: 10 golden, 5 refusal, 5 PII → `logs/eval_results.json` |

**Fault injection (required).** Two seeded cases, because they test the highest-severity risks:

1. Feed Stage 6 a `min_sip` chunk from ELSS (₹500) while the question asks about Large Cap. `grounded` must reject ₹500 for that query.
2. Feed a `nav` chunk for AMC-level `Total AUM` when the question asks fund AUM. Must be rejected or explicitly re-scoped as AMC-level.

Pass criteria are PRD §11: ≥ 9/10 golden, 5/5 refusals, 0 PII persisted, 100% answers ≤3 sentences with a `Last updated` line, and both fault-injection cases caught.

---

## 10. Dependencies

```
chromadb>=0.5            vector store (Stage 4)
sentence-transformers    embedding model (Stage 3)
torch                    transitive, required by sentence-transformers
numpy                    vector handling
trafilatura              main-content extraction (Stage 1)
beautifulsoup4 + lxml    HTML parsing, selector-based noise strip
requests                 HTTP (Stage 1)
pydantic>=2              models + config validation
PyYAML                   config.yaml
tiktoken                 token counting for chunk sizing
streamlit                UI (§7)
pytest                   test suite
```

Deliberately excluded: no framework (LangChain/LlamaIndex) — for a 6-stage pipeline this size, explicit modules are clearer to grade and easier to demo stage by stage. No web-search tool, since the corpus is a closed allowlist.

---

## 11. Deployment

**Local (default).**
```bash
pip install -r requirements.txt
python scripts/ingest.py        # Stages 1-4, writes logs/ingest_run.json
streamlit run app/streamlit_app.py
```

**Hosted prototype link.** Deploy the Streamlit app; `chroma_db/` is built at startup if absent. Add `HF_TOKEN`/API key as a Streamlit secret, never in code. Requires the model download at first boot, so the first launch is slow — pre-warm before the demo.

**Demo mode.** `config.project.mode: snapshot` (P8) with `data/raw/` committed means the demo never touches the network for ingestion. `python scripts/ingest.py --live` re-fetches and updates `fetched_at`, which then flows into every `Last updated from sources:` line.

---

## 12. Decision Log (ADR-style)

| # | Decision | Alternatives rejected | Why |
|---|---|---|---|
| A1 | Label-aware recursive chunking | Semantic, fixed-size, whole-page | R1–R4 verdict (§7.2.1). Semantic adds cost + non-determinism and groups unrelated labels; whole-page defeats per-fact retrieval |
| A2 | `chunk_size=200` tokens | 256, 320, 512 | MiniLM truncates at 256; prefix + specials leave ~230. 320 would silently truncate — the PRD originally said 320 and was corrected |
| A3 | Prefix-enriched `embed_input` | Raw `text` | Short chunks ("Min. for SIP ₹500") are otherwise ambiguous across 5 near-identical pages |
| A4 | `scheme_id` metadata filter | Rely on ranking to disambiguate | Ranking is probabilistic; a filter is deterministic. Guards against the corpus's core hazard |
| A5 | Verbatim-value grounding check | LLM self-critique only | A deterministic string check catches wrong-scheme attribution; self-critique does not reliably |
| A6 | Guards before retrieval | After retrieval / after generation | PII must never reach Chroma or the LLM (P2) |
| A7 | Explicit 2-layer advice guard | Lexicon only, or classifier only | Lexicon leaks on paraphrase; classifier alone is non-deterministic and slow |
| A8 | Chroma with manual MMR | Native MMR, or no rerank | Native MMR is not uniformly available across Chroma versions; no rerank lets near-duplicates crowd out the answer |
| A9 | Deterministic chunk ids + upsert | Random UUIDs | Re-ingest must not duplicate the corpus (P4) |
| A10 | Snapshot-first ingestion | Live-only | Demo reliability; `--live` retains freshness (P8) |
| A11 | No RAG framework | LangChain / LlamaIndex | Explicit stages are what the assignment is grading |
| A12 | Link-out for statement downloads (FR12) | Adding a 6th source | PRD §9.1: outside the locked 5-URL allowlist; a link-out is honest, an invented answer is not |

---

## 13. Traceability — PRD → Module → Test

| PRD ref | Module | Test |
|---|---|---|
| FR1 ingest 5 pages | `pipeline/stage1_load.py` | `test_stage1_load.py::test_five_docs` |
| FR2 fact-level chunks + metadata | `pipeline/stage2_chunk.py` | `test_stage2_chunk.py::test_gold_facts_extracted` |
| FR3 MiniLM → Chroma | `stage3_embed.py`, `stage4_store.py` | `test_stage3_embed.py`, `test_stage4_store.py` |
| FR4 answer all fact families | `stage6_generate.py` + prompt | `scripts/evaluate.py` golden set |
| FR5 exactly one citation | `guards/validator.py` | `test_validator.py::test_citation_presence` |
| FR6 `Last updated` line | `stage6_generate.py` | `test_validator.py::test_last_updated` |
| FR7 refuse advice | `guards/intent.py` | `test_guards.py::test_advice_refused` |
| FR8 refuse PII pre-retrieval | `guards/pii.py` | `test_guards.py::test_pii_blocked_unlogged` |
| FR9 decline returns | `guards/performance.py` | `test_guards.py::test_performance_declined` |
| FR10 "not available" not guessing | `stage5_retrieve.py` sim_floor | `test_pipeline_integration.py::test_sim_floor` |
| FR11 ELSS lock-in ₹500 | `stage2_chunk.py` registry | `test_stage2_chunk.py::test_elss_fields` |
| FR12 statement link-out | `stage6_generate.py` | golden Q11 |
| FR13 UI shell + disclaimer | `app/streamlit_app.py` | manual / streamlit smoke |
| FR14 idempotent re-ingest | `stage4_store.py` | `test_stage4_store.py::test_idempotent` |
| FR15 semantic-chunking flag | `stage2_chunk.py` selector | `test_stage2_chunk.py::test_semantic_reachable` |
| S1 no PII | `guards/pii.py` | `test_guards.py` |
| S2 no advice | `guards/intent.py` | `test_guards.py` |
| S3 no performance claims | `guards/performance.py` | `test_guards.py` |
| S4 public sources only | `config.py` allowlist | `test_stage1_load.py::test_allowlist_rejects_extra_url` |
| S5 ≤3 sentences, 1 citation, date | `guards/validator.py` | `test_validator.py` |
| S6 no fabricated citations | `guards/validator.py` | `test_validator.py::test_citation_allowed` |
| S7 honest staleness | `observability/runlog.py` | `test_stage1_load.py::test_fetched_at_recorded` |
| §3.2 #1–5 disambiguation | `retrieval/aliases.py`, prompt constants | `test_aliases.py`, fault injection 1 & 2 |
| §6.2 R1–R4 decision log | `stage2_chunk.py` selector | `test_stage2_chunk.py::test_rules_verdict` |
| §10 transparency panel | `observability/trace.py` | `test_pipeline_integration.py::test_trace_complete` |
| §11 eval pass criteria | `scripts/evaluate.py` | `logs/eval_results.json` |

**Coverage check:** all 15 FRs, all 8 S-requirements, and all 5 disambiguation rules map to a module and a named test. No orphan requirement; no test without a requirement.

---

## 14. Build Order

Aligned to PRD §13 milestones so each stage is independently demoable.

| Step | Milestone | Files created | Exit check |
|---|---|---|---|
| 1 | M0 scope lock | `config.yaml`, `eval/golden_qa.json` | Allowlist loaded; 10 golden queries with expected values |
| 2 | M1 Loading | `models.py`, `stage1_load.py`, `scripts/seed_snapshot.py` | 5 clean text files |
| 3 | M2 Chunking | `stage2_chunk.py`, `observability/runlog.py` | All gold facts extracted; audit passes; R1–R4 logged |
| 4 | M3 Embedding | `retrieval/embedder.py`, `stage3_embed.py` | 384-dim unit vectors; no-truncation assert green |
| 5 | M4 Store | `stage4_store.py` | Sample fact query returns the right chunk |
| 6 | M5 Retrieve + guard | `guards/*`, `retrieval/aliases.py`, `rerank.py`, `stage5_retrieve.py` | Guards fire; `equity fund → flexi cap` works |
| 7 | M6 Generate + UI | `llm/client.py`, `stage6_generate.py`, `app/streamlit_app.py` | Validated answers with citations in the UI |
| 8 | M7 Validate | `tests/*`, `scripts/evaluate.py` | 10/10 golden, 5/5 refusals, 2/2 fault injection |
| 9 | M8 Docs | `README.md`, `sources.csv`, `SAMPLE_QA.md` | Deliverables complete |

Effort note carried over from PRD §13: steps 2–5 are ingestion and where correctness is won. Step 7 gets the visible polish, but step 3's label registry and step 8's fault injection are what make the answers *correct* — do not let step 7 start before step 3's audit is green.
