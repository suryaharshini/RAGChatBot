# IMPLEMENTATION GUIDE — Phase-wise Build Plan

| | |
|---|---|
| **Project** | Groww MF-Guide — class project |
| **Document** | Agent-executable build plan derived from `architecture.md` |
| **Owner** | Surya Harshini Gundreddy |
| **Date** | 27 Sep 2026 |
| **How to use** | Hand this file + `architecture.md` + `PRD.md` to opencode. Execute **Phase 0 → 8 in order**, one phase per session. Do not start a phase until the previous phase's acceptance criteria pass. |

---

## 0. Setup & Deployment

### Local run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
python -m scripts.ingest          # builds the Chroma index from the 5 allowlisted pages
./run_app.sh                      # http://localhost:8501
```

`scripts/ingest.py` re-fetches from the allowlist when `data/raw/` is absent, so a
fresh clone works with no extra flags. `chroma_db/` and `data/raw/` are
gitignored, so **ingest must run before the app can answer anything**.

### Environment variables

Set these wherever the app runs. There is no `.env.example` in the repository
(it is untracked), so record them here instead:

| Variable | Value | Required |
|---|---|---|
| `LLM_PROVIDER` | `groq` | yes — omit or set `fake` for canned responses |
| `LLM_MODEL` | `qwen/qwen3.8-27b` | yes |
| `GROQ_API_KEY` | your key | yes when `LLM_PROVIDER=groq` |

Locally these go in a gitignored `.env`, which the app loads via
`python-dotenv`. Stages 1–4 (ingest) never touch these, so ingest is safe at
build time with no secrets present.

### Render

Create a **Web Service** and point it at `suryaharshini/RAGChatBot`. Render has
no root-directory setting for a single-repo service; leave it at the repo root.

| Setting | Value |
|---|---|
| Build Command | `pip install -r requirements.txt && python -m scripts.ingest` |
| Start Command | `streamlit run app/streamlit_app.py --server.port $PORT` |
| Health Check Path | `/_stcore/health` |

Environment: **no manual setting needed.** Python is pinned by `.python-version`
(`3.12.14`), which Render reads at build time. Do not add a `runtime.txt` —
Render ignores it, and the build silently falls back to Render's default
Python, which is currently 3.14.

Environment variables: the three from the table above, entered in the Render
dashboard as secrets.

Why the start command looks this way:

- **Do not use `./run_app.sh` on Render.** It hardcodes `.venv/`, which does not
  exist on a Render service, and it exits with setup instructions when the venv
  is missing.
- **No `--server.address 0.0.0.0` needed.** `.streamlit/config.toml` sets
  `address = "0.0.0.0"`, so Streamlit binds all interfaces. Verified that
  `STREAMLIT_SERVER_PORT` (and `--server.port`) still overrides the file's
  `port = 8501`, so Render's injected `$PORT` wins.
- **Ingest belongs in the build**, not at startup, because the Chroma index is
  gitignored. Building it at startup would add a multi-minute cold start and
  race against Render's health check.
- **Do not cache `chroma_db/`.** A cached index can outlive a change to the
  source pages; rebuilding each deploy takes seconds and cannot go stale.

### Sizing

`requirements.txt` points at PyTorch's CPU channel and pins `torch==2.6.0`.
The default Linux `torch` wheel is the CUDA build, which pulls ~2.5GB of
`nvidia-*` packages. The CPU channel's newest cp39 wheel is 2.6.0 — a higher
bound resolves to the CUDA build and defeats the extra index.

Even CPU-only, the dependency tree plus the `all-MiniLM-L6-v2` model download
(~90MB) exceeds Render's free instance (512MB RAM / 0.5GB disk). **Use a paid
instance**; Starter is the realistic floor.

### The `torch==2.6.0` / Python version coupling

`torch==2.6.0` and the Python version are not independent choices. The CPU
channel publishes `2.6.0+cpu` for cp39 through cp313, but **not for cp314**.
On Python 3.14 the build therefore fails with:

```
ERROR: Could not find a version that satisfies the requirement torch==2.6.0
(from versions: 2.9.0, 2.9.1, 2.10.0, 2.11.0, 2.12.0, 2.12.1, 2.13.0, 2.14.0)
```

That list starting at 2.9.0 is the tell: it means the build is running on 3.14
regardless of what the repository pins. Verified resolution for
`torch==2.6.0` with the full dependency set:

| Python | Resolves? |
|---|---|
| 3.11 | yes |
| 3.12 | yes (pinned in `.python-version`) |
| 3.13 | yes |
| 3.14 | **no** — no cp314 wheel |

If a future dependency forces a Python upgrade, either raise the `torch` pin to
2.9.0+ and re-check for `nvidia-*` in a dry run, or keep the version in
`.python-version` in sync with the `torch` pin. To check a candidate before
deploying:

```bash
pip install --dry-run --python-version 3.14 --only-binary=:all: \
  --target /tmp/out -r requirements.txt
```

Confirm the `Would install ...` line lists `torch-<version>+cpu` and no
`nvidia-*` packages.

---

## 1. How to Use This Document

Each phase has six fixed sections:

| Section | Purpose |
|---|---|
| **Goal** | One sentence. What exists at the end that didn't before |
| **Files** | Exact paths to create or modify. Do not create files not listed |
| **Tasks** | Numbered, ordered, independently checkable |
| **Acceptance** | Commands + expected output. Non-negotiable gate |
| **Traps** | Known failure modes specific to this codebase |
| **Prompt** | Copy-paste block to hand opencode for that phase |

**Rules for the executing agent:**

1. **One phase at a time.** Never implement ahead.
2. **Acceptance gates are hard.** If acceptance fails, fix or report. Never proceed with a failing gate and never fake an output to pass.
3. **No fabrication.** If the network blocks a fetch, or a fact is not in the corpus, stop and report. Never invent a page, value, or citation.
4. **Stay inside the file list.** Extra files indicate scope creep; if one seems necessary, ask first.
5. **Report at the end of each phase** with: files created, acceptance output, and anything skipped or blocked.

**Phase tracker** (update as you go):

| Phase | Milestone | Status |
|---|---|---|
| 0 | M0 Scope lock & scaffold | ☑ |
| 1 | M1 Loading | ☑ |
| 2 | M2 Chunking | ☑ |
| 3 | M3 Embedding | ☑ |
| 4 | M4 Store vector data | ☑ |
| 5 | M5 Retrieve + guards | ☑ (see §7.1 deviations) |
| 6 | M6 Generate + validate + UI | ☑ (see §10.1 deviations) |
| 7 | M7 Validation & evaluation | ☐ |
| 8 | M8 Docs & deliverables | ☐ |

---

## 2. Preflight: Environment & Conventions

### 2.1 Environment

- Python 3.11+
- Virtualenv required. Never install into system Python.
- macOS/Linux; commands use POSIX paths.
- First `pip install` downloads the embedding model (~80 MB) — allow time.

```bash
cd /Users/suryaharshinigundreddy/GrowApp
python3 -m venv .venv
source .venv/bin/activate
```

### 2.2 Coding conventions (apply to every phase)

| Convention | Rule |
|---|---|
| Comments | **None.** No inline, block, or docstring comments in code. Document in markdown instead |
| Types | Every function signature fully type-annotated |
| Models | pydantic v2 for cross-stage contracts; `dataclass` for internal value objects |
| Config | No URLs, thresholds, or model IDs hardcoded in modules — all from `config.yaml` |
| Imports | Absolute from `src.` package root |
| Errors | Custom exceptions in `src/models.py`; no bare `except:` |
| Money/text | Never strip `₹`, `%`, `Cr`, parentheses, or `–` during normalisation |
| Style | `ruff`-clean, 100-char lines |

### 2.3 Invariants — never violate, in any phase

1. **Allowlist is the only source of URLs.** One module owns URL validation; both the fetch path and the citation validator call it.
2. **PII never reaches Chroma, the LLM, or any log.**
3. **No advice, no return computation, no return comparison** in any generated text.
4. **Every answer carries exactly one allowlisted citation and a `Last updated from sources:` line.**
5. **Chunk embedding input never exceeds 230 tokens** (256 limit − specials − prefix). Assert, never truncate.
6. **Fund AUM comes from `Fund size (AUM)` only.** The `Total AUM ₹9,86,236.84 Cr` in the About block is HDFC *AMC*-level and is identical on all 5 pages.
7. **An ungrounded question gets "not in my corpus" — never a guess.**

---

## 3. Ground Truth (do not re-derive, do not re-fetch)

Verified from the 5 live pages on **27 Sep 2026**. This is the oracle for every acceptance check. Values marked ⚠ are traps.

| Field | Large Cap | Flexi Cap | ELSS | Small Cap | Balanced Adv. |
|---|---|---|---|---|---|
| `scheme_id` | `hdfc_large_cap` | `hdfc_flexi_cap` | `hdfc_elss` | `hdfc_small_cap` | `hdfc_balanced_advantage` |
| `scheme_name` | HDFC Large Cap Fund Direct Growth | HDFC Flexi Cap Direct Plan Growth | HDFC ELSS Tax Saver Fund Direct Plan Growth | HDFC Small Cap Fund Direct Growth | HDFC Balanced Advantage Fund Direct Growth |
| `category` | Equity Large Cap | Equity Flexi Cap | Equity ELSS | Equity Small Cap | Hybrid Dynamic Asset Allocation |
| NAV (25 Sep '26) | ₹1,189.08 | ₹2,214.57 | ₹1,447.38 | ₹159.82 | ₹557.73 |
| `expense_ratio` | 1.03% | 0.77% | 1.21% | 0.78% | 0.78% |
| `fund_aum` | ₹39,933.37 Cr | ₹1,13,606.47 Cr | ₹15,991.78 Cr | ₹41,890.86 Cr | ₹1,07,295.79 Cr |
| `min_sip` | ₹100 | ₹100 | ⚠ ₹500 | ₹100 | ₹100 |
| `min_first_inv` | ₹100 | ₹100 | ⚠ ₹500 | ₹100 | ₹100 |
| `benchmark` | NIFTY 100 Total Return Index | NIFTY 500 Total Return Index | NIFTY 500 Total Return Index | BSE 250 SmallCap Total Return Index | NIFTY 50 Hybrid Composite Debt 50:50 Index |
| `exit_load` | 1% if redeemed within 1 year | 1% if redeemed within 1 year | ⚠ Nil | 1% if redeemed within 1 year | ⚠ 1% within 1 year, **on units in excess of 15% of the investment** |
| `risk_rating` | Very High | Very High | Very High | Very High | Very High |
| `lock_in` | — | — | ⚠ 3Y | — | — |
| `stamp_duty` | 0.005% | 0.005% | 0.005% | 0.005% | 0.005% |
| `rating` | 4 | 5 | 5 | 3 | 5 |
| `holdings_count` | 50 | 86 | 65 | 87 | 326 |
| `custodian` | HDFC Bank | Deutsche Bank | Deutsche Bank | Citibank NA | HDFC Bank |
| `rta` | Cams | Cams | Cams | Cams | Cams |
| `launch_date` (scheme) | 01-Jan-2013 | 01-Jan-2013 | 01-Jan-2013 | 01-Jan-2013 | 01-Jan-2013 |
| ⚠ `amc_incorp_date` (AMC) | 10 Dec 1999 | 10 Dec 1999 | 10 Dec 1999 | 10 Dec 1999 | 10 Dec 1999 |
| ⚠ `amc_aum_total` | ₹9,86,236.84 Cr | ₹9,86,236.84 Cr | ₹9,86,236.84 Cr | ₹9,86,236.84 Cr | ₹9,86,236.84 Cr |

**Common to all 5:** stamp duty 0.005%; RTA Cams; launch date 10 Dec 1999; and the tax note *"If you redeem within one year, returns are taxed at 20%. If you redeem after one year, returns exceeding Rs 1.25 lakh in a financial year are taxed at 12.5%."*

### 3.1 Traps encoded in the ground truth

| # | Trap | Consequence if mishandled |
|---|---|---|
| T1 | `hdfc-equity-fund-direct-growth` is the **Flexi Cap** scheme | "Equity fund" questions answered with the wrong fund |
| T2 | `Total AUM ₹9,86,236.84 Cr` identical on all 5 pages = **AMC**-level | Fund AUM questions answered with the same wrong number 5× |
| T3 | ELSS is the only scheme with `Nil` exit load, ₹500 minimum, and 3Y lock-in | ELSS answers contaminated by the other four |
| T4 | Small Cap has 4 historical exit-load rows (2013/2014/2015/2018) | A superseded 2%/36-month load presented as current |
| T5 | Balanced Advantage exit load applies only **above 15% of the investment** | Dropped the threshold — materially wrong fee |
| T6 | Pages render label and value with **no whitespace**: `Expense ratio1.03%`, `Min. for SIP₹100`, `Fund benchmarkNIFTY 100 Total Return Index`, `CustodianHDFC Bank` | Label regexes using `\s+` silently match nothing; all gold facts extract as empty |
| T7 | `NAV` in the About prose ("Latest NAV as of 25 Sep 2026 is ₹1,447.38") differs in date format from the header (`NAV: 25 Sep '26`) | Two conflicting NAV strings per page |
| T8 | The page footer's `Date of Incorporation 10 Dec 1999` is the **AMC's** date, not the scheme's. Scheme launch is `01-Jan-2013` | Bot reports the wrong inception date for all 5 schemes |
| T9 | The `fund_manager` scalar is stale (`Prashant Jain` for Large Cap). Real managers are in `fund_manager_details[].person_name` | Golden Q10 fails; reports a former manager as current |
| T10 | `stp_in_minimum_installment_amount` is ₹1,000 but the page displays `Min. for 2nd investment ₹100` (= `mini_additional_investment`) | Bot quotes a 10× wrong minimum for 4 of 5 schemes |

**T6 is the highest-probability failure in a text-scraping build.** Label regexes using `\s+` silently match nothing. Note: when extraction reads the page's embedded `__NEXT_DATA__` payload instead of rendered text, T6/T7 disappear entirely — see §5.1.

### 3.2 Extraction path decided in Phase 1 (supersedes the Phase 1 spec)

The Phase 1 brief specified `trafilatura` text extraction. A live probe during Phase 1 showed that loses required facts: trafilatura returned 1,872 chars for a page whose rendered text is far larger, and it **dropped** the exit-load label, the ELSS `3Y Lock-in` badge, the stamp-duty value, `Custodian`, `Registrar & Transfer Agent`, all holdings, and the exit-load history rows. Two acceptance criteria (`exit_load`, `lock_in`) were therefore unextractable by that route.

Each page embeds its own public data payload in a `<script id="__NEXT_DATA__">` block, which contains every gold fact with exact values. Phase 1 was therefore implemented as:

| Path | Order | Used when |
|---|---|---|
| **Structured** — parse `__NEXT_DATA__` → `mfServerSideData`, normalise to `facts.json`, then render a clean `label: value` document | primary | payload present (all 5 pages) |
| **Text** — `trafilatura` + BeautifulSoup noise strip | fallback | payload absent, e.g. a layout change |

This is still **public-sources-only** — it is the same public page, read from its own embedded data, with no third-party source and no back-end access. It satisfies architecture principle P3 (*"Extract, don't scrape"*) far more literally than the regex approach, and it makes the Phase 2 label registry deterministic because the rendered text uses a single unambiguous `label: value` form.

Consequences for later phases:

- Phase 2's `LABEL_REGISTRY` still works, but its patterns can be simple `\s*` splits on clean `label: value` lines. T6 is no longer a live risk.
- `data/processed/facts.json` becomes a second, exact input Phase 2 may consult. Prefer `facts.json` values over re-parsing text when the field is present there.
- The trafilatura path must stay working and is covered by a test, in case the site drops the payload.
- T8, T9, T10 above were all discovered only via the structured path. A text-only build would have silently carried the wrong values.

---

## 4. Phase 0 — Scope Lock & Scaffold

**Goal:** A validated config, typed models, and the three eval datasets exist; nothing is fetched yet.

**Depends on:** nothing.

### Files

```
requirements.txt
config.yaml
.gitignore
src/__init__.py
src/config.py
src/models.py
eval/golden_qa.json
eval/refusal_qa.json
eval/pii_cases.json
data/raw/.gitkeep
logs/.gitkeep
```

### Tasks

1. `requirements.txt` — exactly the deps in `architecture.md` §10. Pin minimum versions only.
2. `.gitignore` — `.venv/`, `__pycache__/`, `*.pyc`, `.env`, `chroma_db/`, `data/raw/`, `*.npz`. Do **not** ignore `data/processed/` or `logs/`.
3. `config.yaml` — reproduce `architecture.md` §6 **verbatim and complete**. Do not trim keys; later phases read them.
4. `src/config.py` — pydantic models for the config (`ProjectConfig`, `SourcesConfig`, `LoadConfig`, `ChunkConfig`, `EmbedConfig`, `StoreConfig`, `RetrieveConfig`, `GenerateConfig`, `GuardsConfig`). Expose `load_config(path) -> AppConfig`, cached. Validate the allowlist is exactly 5 entries with unique `scheme_id` and unique `url`.
5. `src/models.py` — pydantic models from `architecture.md` §5.1–5.3: `Document`, `Chunk`, `GuardDecision`, `SchemeResolution`, `RetrievedChunk`, `ValidationResult`, `Answer`, `Trace` (with `stage1`…`stage6` sub-objects). Plus `ChunkParams`, `StrategyDecision` dataclasses, and exceptions `IngestError`, `AllowlistViolation`, `FetchError`, `ExtractionError`, `GuardTripped`, `ValidationFailed`.
6. `src/pipeline/__init__.py` and the remaining package `__init__.py` files (empty).
7. `eval/golden_qa.json` — 10 entries from `PRD.md` §11 with fields `{id, query, scheme_id, expected_value, expected_source_url, field}`.
8. `eval/refusal_qa.json` — the 5 refusal queries from `PRD.md` §11 with `{id, query, expected_refusal_type}` where `refusal_type ∈ {advice, performance, out_of_scope}`.
9. `eval/pii_cases.json` — 5 PII strings with `{id, label, text}`. These are **synthetic test fixtures only**; they are never written to any log.

### Acceptance

```bash
source .venv/bin/activate
pip install -r requirements.txt
python -c "from src.config import load_config; c=load_config(); print(len(c.sources.allowlist), c.chunk.size_tokens, c.embed.dim)"
```

Expected: `5 200 384`

```bash
python -c "import json; [print(f, len(json.load(open(f)))) for f in ['eval/golden_qa.json','eval/refusal_qa.json','eval/pii_cases.json']]"
```

Expected: counts `10`, `5`, `5`. Every `expected_source_url` in `golden_qa.json` must be in the allowlist — assert this in a one-off check, not a permanent test.

### Traps

- Chroma/pydantic versions drift. Pin `pydantic>=2`; use pydantic v2 APIs (`model_validate`, `model_dump`).
- `config.yaml` must include `generate.max_sentences: 3` and `retrieve.sim_floor: 0.30`. Phase 6 and 5 depend on them.

### Prompt

> Read `architecture.md` §2, §5, §6 and `PRD.md` §3, §11. Execute **Phase 0** of `implementation.md`. Create only the files listed in Phase 0's Files block. Reproduce `config.yaml` verbatim from `architecture.md` §6. Build the pydantic models from §5.1–5.3 with full type annotations and no comments. Populate the three eval JSON files from `PRD.md` §11 and `implementation.md` §3. Then run the two acceptance commands and paste the real output. If any acceptance check fails, fix it and re-run — do not report success on a failure.

---

## 5. Phase 1 — Stage 1: Loading

**Goal:** 5 clean text files in `data/raw/`, each containing its own gold facts, with all nav and widget noise gone.

**Depends on:** Phase 0.

### Files

```
src/pipeline/stage1_load.py
scripts/seed_snapshot.py
data/raw/hdfc_large_cap.txt
data/raw/hdfc_flexi_cap.txt
data/raw/hdfc_elss.txt
data/raw/hdfc_small_cap.txt
data/raw/hdfc_balanced_advantage.txt
```

### Tasks

1. `url_is_allowed(url) -> bool` and `assert_allowed(url)`. Normalise first: lowercase scheme+host, strip query string and fragment, strip trailing slash. Compare against the allowlist. Raise `AllowlistViolation` on mismatch. This is the single owner of URL validation — the citation validator in Phase 6 imports it.
2. `fetch(url, session) -> str` with explicit `User-Agent` from config, `timeout_s`, and `max_retries` with exponential backoff. Raise `FetchError` after retries are exhausted.
3. `extract_main_text(html) -> str` using `trafilatura` with `include_tables=False`; fall back to `<main>` then the largest `<div>`. Raise `ExtractionError` if the result is shorter than `load.min_text_chars`.
4. `strip_noise(text) -> str`:
   - BeautifulSoup pass removing every selector in `load.drop_selectors`.
   - Then six regex removals for the labelled noise blocks, matched loosely on their headings: `Compare similar funds`, `Return calculator`, `Understand terms`, `See All`, the market-ticker sidebar, and the product mega-menu.
5. `normalize(text) -> str` — collapse runs of whitespace to single spaces, unify dashes to `-`, **preserve** `₹`, `%`, `Cr`, `(`, `)`, `,` in numbers. Never alter digit sequences.
6. `build_document(scheme, text, fetched_at) -> Document` — compute `text_sha256`.
7. `load_all(config, live: bool) -> list[Document]` — when `live=False`, read from `data/raw/{scheme_id}.txt` and use the file mtime as `fetched_at`; when `live=True`, fetch then persist. Return exactly 5 documents or raise.
8. `scripts/seed_snapshot.py` — CLI: `--live` to fetch and write the 5 snapshots, default to verify existing snapshots parse.
9. **Verification oracle.** Add `verify_documents(docs) -> None` asserting, per `scheme_id`, that the text contains: the `scheme_name`, the `expense_ratio` value, the `min_sip` value, the `benchmark` string, and the `exit_load` value from §3. Missing any → raise `ExtractionError` naming the scheme and field. Call it at the end of `load_all`.

### Acceptance

```bash
python scripts/seed_snapshot.py --live
python -c "from src.pipeline.stage1_load import load_all; from src.config import load_config; d=load_all(load_config()); [print(x.scheme_id, len(x.text)) for x in d]"
```

Expected: 5 lines, one per `scheme_id`, each `len(text) > 1500`.

```bash
python -c "
from src.pipeline.stage1_load import load_all
from src.config import load_config
for d in load_all(load_config()):
    print(d.scheme_id, 'noise' if 'Compare similar funds' in d.text or 'Return calculator' in d.text else 'clean')
"
```

Expected: `clean` on all 5.

Then **manually confirm** these strings exist in the right files (this is the check that catches T6):

| File | Must contain (possibly with no space, see T6) |
|---|---|
| `hdfc_large_cap.txt` | `HDFC Large Cap Fund Direct Growth`, `1.03%`, `₹100`, `NIFTY 100 Total Return Index` |
| `hdfc_flexi_cap.txt` | `HDFC Flexi Cap Direct Plan Growth`, `0.77%`, `NIFTY 500 Total Return Index` |
| `hdfc_elss.txt` | `HDFC ELSS Tax Saver Fund Direct Plan Growth`, `1.21%`, `₹500`, `Nil`, `3Y Lock-in` |
| `hdfc_small_cap.txt` | `HDFC Small Cap Fund Direct Growth`, `0.78%`, `BSE 250 SmallCap Total Return Index` |
| `hdfc_balanced_advantage.txt` | `HDFC Balanced Advantage Fund Direct Growth`, `0.78%`, `NIFTY 50 Hybrid Composite Debt 50:50 Index`, `excess of 15% of the investment` |

If the live fetch is blocked (network, robots, layout change): **stop and report.** Do not hand-write the text files from memory. The fallback is to ask the user for a saved copy of the pages.

### Traps

- **T6:** the pages render `Expense ratio1.03%` with no space. Any regex like `Expense ratio\s+(\S+)` fails. Prefer DOM-node selection; if regex, use `Expense ratio\s*`.
- `trafilatura` may return an empty string on JS-heavy pages. The `<main>` fallback exists for this.
- Do not strip the `Compare similar funds` **values** — only that block. It is a reliable noise marker.
- `fetched_at` must be a real date, not a placeholder. G5 depends on it.

### Prompt

> Read `architecture.md` §7.1 and `implementation.md` §3.1 (traps T1–T7) and Phase 1. Execute **Phase 1** only. Create `src/pipeline/stage1_load.py` and `scripts/seed_snapshot.py`. Critically: the source pages concatenate label and value with **no whitespace** (`Expense ratio1.03%`), so your extraction must tolerate zero whitespace — prefer DOM-node selection, and use `\s*` in any regex fallback. Implement `verify_documents` using the ground-truth table in `implementation.md` §3 and make `load_all` call it. Run the acceptance commands, paste the real output, and manually confirm the five "must contain" tables. If the fetch fails, stop and report the HTTP error — do not fabricate content.

---

## 6. Phase 2 — Stage 2: Chunking

**Goal:** every gold fact extracted with its exact value as its own chunk, R1–R4 verdict logged, ingest audit green. Realised total: **152 chunks** (see the deviation note in §6.1).

**Depends on:** Phase 1 (all 5 documents must load).

### 6.1 Deviation: 152 chunks, not ~70–90 — 1 fact = 1 chunk

The plan estimated 70–90 chunks, which assumed some sections would be merged into one
multi-fact chunk (e.g. `Min. for 1st` + `2nd` + `SIP` in a single "Minimum investments"
chunk, as sketched in `architecture.md` §7.2.1). That was rejected on correctness grounds:

- **T3 is a contamination bug, not a cosmetic one.** Bundling `exit_load` beside
  `lock_in` and `stamp_duty` means a query for "lock-in period" retrieves a chunk that
  also says "Exit load: Nil". A 3-way split is exactly what makes that confusion
  impossible, so each fact gets its own chunk.
- **Retrieval precision is the whole point of FR9.** 152 chunks over 5 short documents is
  a trivial corpus for ChromaDB and costs nothing, while every chunk stays a single
  self-contained answer unit.
- The earlier estimate also predated the exact field inventory. 24 label-registry fields
  × 5 schemes = 120, plus 14 `fund_manager`, 5 `top_holding`, 5 `sector_allocation`,
  and 8 `exit_load_historical` rows.

**Consequence:** the current exit-load row is deliberately *not* re-emitted as an
`exit_load_historical` chunk. `exit_load` carries the current load; only superseded rows
become history chunks (large 2, flexi 0, elss 0, small 3, balanced 3 = 8). This removes
the "which row is current?" ambiguity at the source.

**Accepted trade-off:** the largest chunk is 112 tokens, well under the 200 budget, so
`chunk_prose` is never invoked on this corpus. It remains the enforced fallback and is
tested directly (537-token input → 4 chunks, sizes `[197, 195, 195, 66]`, overlap intact).

### Files

```
src/pipeline/stage2_chunk.py
src/observability/__init__.py
src/observability/runlog.py
data/processed/chunks.jsonl
logs/ingest_run.json
```

### Tasks

1. `LABEL_REGISTRY` — a list of specs, one per gold fact. Each spec: `field`, `section`, `patterns` (list of regexes, each with a named group `value`), `value_type ∈ {currency, percent, currency_cr, index_name, int, text, duration, enum}`, `scope: fund|amc|scheme`, and `is_fact: bool`. Cover every field in §3: `nav`, `expense_ratio`, `fund_aum`, `min_sip`, `min_first_inv`, `min_second_inv`, `benchmark`, `exit_load`, `stamp_duty`, `tax_note`, `risk_rating`, `category`, `rating`, `holdings_count`, `lock_in`, `launch_date`, `custodian`, `rta`, `fund_house`, `investment_objective`, `amc_aum_total`, `top_holding`, `fund_manager`.
2. **Every pattern must use `\s*` between label and value** (T6). Add a unit-level self-test that asserts every registry pattern compiles and that a synthetic `Expense ratio1.03%` string matches `expense_ratio`.
3. `amc_aum_total` must set `scope: amc` and its chunk text must be prefixed so the AMC level is explicit in the retrieved text.
4. `parse_exdated_exit_loads(text, scheme_id)` — the exit-load block is a dated list. Emit one `exit_load` chunk with `historical: false` for the **latest** effective date, and one `exit_load_historical` chunk per earlier row with `historical: true` and `effective_date` set. Small Cap has 4 rows; the others have 1–2.
5. `parse_holdings(text)` — with `chunk.holdings_mode: sector_summary`, emit `sector_allocation` chunks per sector and one `top_holding` chunk listing the top 5 holdings with weights. Never emit a 300-row table.
6. `parse_fund_managers(text)` — one `fund_manager` chunk per manager with name, tenure start, and qualification line.
7. `chunk_prose(text)` — for residual prose (About block, manager bios), use `RecursiveCharacterTextSplitter.from_tiktoken_encoder(encoding_name="cl100k_base")` with `size_tokens=200`, `overlap_tokens=40`, and the configured separators.
8. `build_chunk_id(doc, field, n) -> str` — deterministic `{scheme_id}_{section}_{field}_{n}` (idempotency, architecture P4).
9. `select_chunking_strategy(corpus_stats) -> StrategyDecision` — evaluate R1–R4 per `architecture.md` §7.2.1, return the dataclass with `selected`, `rejected`, `rules`, `rationale`, `params`. Must select `label_aware_recursive` and reject `semantic` on this corpus. Must honour `chunk.strategy: semantic` when set explicitly (FR15).
10. `chunk_documents(docs, params) -> list[Chunk]` — parse registry fields first, then prose fallback. Set `embed_input = prefix_template.format(scheme_name=…, category=…, text=…)`. Set `is_fact`, `historical`, `effective_date`, `source_url`.
11. `audit_chunks(chunks) -> AuditResult` — the six checks in `architecture.md` §7.2.4: all gold facts present with expected values, no chunk over `size_tokens`, no noise marker present, no `label: value` split, every `source_url` allowlisted, and the 5 schemes produce the expected number of distinct values per field. **Derive that expected count from `GOLD_TRUTH`, never hardcode 5** — `expense_ratio` legitimately yields 4 (small_cap and balanced_advantage are both `0.78%`), `benchmark` yields 4 (flexi and elss share NIFTY 500 TRI), and `exit_load` yields 3 (large, flexi and small all read "1% if redeemed within 1 year"). A hardcoded 5 reports false failures.
12. `write_chunks_jsonl(chunks)` and `write_ingest_run_log(decision, chunks, audit, duration)` in `observability/runlog.py`.

### Acceptance

```bash
python -c "
from src.pipeline.stage1_load import load_all
from src.pipeline.stage2_chunk import chunk_documents, audit_chunks
from src.config import load_config
c = load_config()
ch = chunk_documents(load_all(c), c.chunk)
print('total', len(ch))
a = audit_chunks(ch)
print('audit_passed', a.passed, a.failures)
"
```

Expected: `total` 152 (30 / 28 / 28 / 31 / 35); `audit_passed True []`; `facts_vs_text_disagreements []`.

```bash
python -c "
from src.pipeline.stage1_load import load_all
from src.pipeline.stage2_chunk import chunk_documents
from src.config import load_config
c = load_config()
for ch in chunk_documents(load_all(c), c.chunk):
    if ch.field in ('expense_ratio','min_sip','benchmark','exit_load','fund_aum','lock_in'):
        print(ch.scheme_id, ch.field, repr(ch.value), ch.historical)
"
```

Expected — every value must match §3 exactly:

```
hdfc_large_cap expense_ratio '1.03%' False
hdfc_large_cap min_sip '₹100' False
hdfc_large_cap fund_aum '₹39,933.37 Cr' False
hdfc_large_cap benchmark 'NIFTY 100 Total Return Index' False
hdfc_large_cap exit_load 'Exit load of 1% if redeemed within 1 year' False
hdfc_flexi_cap expense_ratio '0.77%' False
hdfc_elss expense_ratio '1.21%' False
hdfc_elss min_sip '₹500' False
hdfc_elss exit_load 'Nil' False
hdfc_elss lock_in '3Y' or '3 years' False
hdfc_small_cap benchmark 'BSE 250 SmallCap Total Return Index' False
hdfc_balanced_advantage benchmark 'NIFTY 50 Hybrid Composite Debt 50:50 Index' False
```

Plus: `hdfc_small_cap` must emit **3** `exit_load_historical` chunks (T4), and `amc_aum_total` must equal `₹9,86,236.84 Cr` on all 5 with `scope == 'amc'`.

```bash
python -c "import json; d=json.load(open('logs/ingest_run.json')); print(d['decision']['selected'], d['decision']['rules'], d['decision']['rejected'])"
```

Expected: `label_aware_recursive {'R1': True, 'R2': True, 'R3': True, 'R4': True} ['semantic']`

### Traps

- **T4:** Small Cap's 2013/2014/2015 rows are historical. Only the 2015-08-05 row (1% within 1 year) is current. Getting this wrong reports a 36-month exit load that no longer exists.
- **T5:** the Balanced Advantage exit load string must retain `excess of 15% of the investment`.
- **T2:** `fund_aum` and `amc_aum_total` are different fields. The audit's "5 distinct `fund_aum`" check is what proves you didn't conflate them.
- `tax_note` and `investment_objective` are long prose. Do not let them swallow neighbouring facts — one fact per chunk.
- The Balanced Advantage page has 326 holdings and ~100 KB of debt lines. This is the page most likely to blow up chunk count or extraction time.

### Prompt

> Read `architecture.md` §7.2 (all subsections) and `implementation.md` §3. Execute **Phase 2** only. Build the `LABEL_REGISTRY` so that every field in `implementation.md` §3 is extractable. Two hard requirements: (1) every regex must tolerate **zero whitespace** between label and value because the pages render `Expense ratio1.03%`; (2) the Small Cap page has 4 dated exit-load rows of which only the 2015-08-05 row is current — emit the other 3 with `historical: true`. Keep `fund_aum` (from `Fund size (AUM)`) strictly separate from `amc_aum_total` (`₹9,86,236.84 Cr`, AMC-level, identical on all 5 pages). Implement `select_chunking_strategy` returning a `StrategyDecision` with the R1–R4 verdicts, and `audit_chunks` with all six checks from `architecture.md` §7.2.4. Run all three acceptance commands and paste the real output. If `audit_passed` is False, fix the cause and re-run — do not suppress the check.

---

## 7. Phase 3 — Stage 3: Embedding

**Goal:** Every chunk has a 384-dim unit-norm vector, and the no-truncation assertion passes.

**Depends on:** Phase 2 audit green.

### Files

```
src/retrieval/__init__.py
src/retrieval/embedder.py
src/pipeline/stage3_embed.py
data/chunks.embeddings.npz
```

### Tasks

1. `get_embedder()` — module-level `lru_cache` singleton returning `SentenceTransformer(config.embed.model_id, device="cpu")`. Set `model.max_seq_length` explicitly from config and **verify** it is 256; raise at init if the loaded model reports a different limit.
2. `embed_input(chunk) -> str` — return `chunk.embed_input`, which Phase 2 already built from the prefix template. Assert it is non-empty and contains the scheme name.
3. `TOKEN_BUDGET = max_seq_length - 2 - 24 = 230` and `assert_no_truncation(chunks, tokenizer) -> None` — measure `len(tokenizer(chunk.embed_input)["input_ids"])` for every chunk; if any exceeds the budget, raise `IngestError` listing the offending `chunk_id`s. This converts a silent correctness bug into a loud failure.
4. `embed_chunks(chunks) -> np.ndarray` of shape `(N, 384)`, dtype `float32`, via `model.encode(texts, batch_size, normalize_embeddings=True, convert_to_numpy=True)`. Pass `truncate_dim=None` so we never silently shorten.
5. `embed_query(text) -> np.ndarray` shape `(384,)`, same model and normalisation, so query and document vectors live in the same space.
6. Cache: `load_or_compute_cache(chunks)` keyed by `text_sha256` + `model_id`; save to `data/chunks.embeddings.npz`. Return the cache on an unchanged corpus so Phase 7 iteration is fast.
7. `pipeline/stage3_embed.py` — orchestrate 3→5, call the assertion first, return a `Stage3Result(vectors, model_id, dim, truncation_assert_passed, cache_hit)`.

### Acceptance

```bash
python -c "
import numpy as np
from src.config import load_config
from src.pipeline.stage1_load import load_all
from src.pipeline.stage2_chunk import chunk_documents
from src.pipeline.stage3_embed import embed_chunks
c = load_config()
ch = chunk_documents(load_all(c), c.chunk)
r = embed_chunks(ch)
print(r.vectors.shape, r.dim, r.truncation_assert_passed, r.model_id)
print('unit_norm', bool(np.allclose(np.linalg.norm(r.vectors, axis=1), 1.0, atol=1e-3)))
print('max_tokens', max(x.token_count for x in ch))
"
```

Expected:
- shape `(N, 384)` where N equals the Phase 2 total
- `truncation_assert_passed True`
- `model_id sentence-transformers/all-MiniLM-L6-v2`
- `unit_norm True`
- `max_tokens` ≤ 230

**Negative test** — the assertion must actually fire:

```bash
python -c "
from src.config import load_config
from src.retrieval.embedder import get_embedder, TOKEN_BUDGET, assert_no_truncation
from src.models import Chunk
fake = Chunk(chunk_id='oversize', scheme_id='hdfc_elss', text='x '*5000, embed_input='x '*5000, source_url='u', section='s', field='f', is_fact=False, historical=False, token_count=9999)
try:
    assert_no_truncation([fake], get_embedder().tokenizer)
    print('FAIL: assertion did not fire')
except Exception as e:
    print('PASS: assertion fired')
"
```

Expected: `PASS: assertion fired`

### Traps

- The first call downloads the model. Time it; do not assume a hang is a bug.
- `normalize_embeddings=True` and a `cosine` Chroma space must agree. If either changes, change both.
- Do not raise `chunk_size_tokens` above 230. The earlier PRD value of 320 would have silently truncated every long chunk.
- MiniLM truncates rather than erroring. The assertion is the only thing standing between you and a confidently-wrong answer.

### Prompt

> Read `architecture.md` §7.3 and `implementation.md` §7. Execute **Phase 3** only. Create `src/retrieval/embedder.py` and `src/pipeline/stage3_embed.py`. The critical requirement: MiniLM-L6-v2 truncates at 256 wordpiece tokens **silently**. Assert `len(tokenizer(embed_input)) <= 230` for every chunk and raise `IngestError` on violation — never let the model truncate. Embed `chunk.embed_input` (which already carries the scheme-name and category prefix), not the raw `text`. Use `normalize_embeddings=True` so all vectors are unit norm, and confirm that with `np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-3)`. Verify `model.max_seq_length == 256` at init. Run both acceptance checks including the negative test that proves the assertion fires on an oversized chunk, and paste the real output.

---

## 8. Phase 4 — Stage 4: Store Vector Data

**Goal:** A persistent Chroma collection that upserts idempotently, supports `scheme_id` filtering, and is driven by one `ingest.py` command.

**Depends on:** Phase 3 acceptance green.

### Files

```
src/pipeline/stage4_store.py
scripts/ingest.py
data/processed/documents.jsonl
chroma_db/
```

### Tasks

1. `get_client(config)` → `chromadb.PersistentClient(path=config.store.path)`.
2. `get_or_create_collection(client, config)` → named `hdfc_mf_faq`, `metadata={"hnsw:space": "cosine"}`. If it exists, keep it; do not drop on rerun.
3. `chunk_to_chroma(chunk) -> dict` with `id=chunk.chunk_id`, `document=chunk.text`, and `metadata` containing only `scheme_id`, `section`, `field`, `value`, `source_url`, `effective_date`, `historical`, `is_fact`, `fetched_at`. **Chroma metadata accepts only `str`, `int`, `float`, `bool` — coerce or omit every `None`.** Never pass Python `None`.
4. `store_chunks(chunks, config) -> StoreResult` using `collection.upsert(...)` (not `add`) so re-runs replace rather than duplicate.
5. `query_collection(config, query_text, scheme_ids=None, n_results=6)` — build the `where` clause as `{"scheme_id": {"$in": scheme_ids}}` when `scheme_ids` is provided, else omit. Return documents, metadatas, and distances.
6. `scripts/ingest.py` — the single orchestrator. Flags: `--live` (re-fetch, default is snapshot), `--chunk-strategy {auto,label_aware_recursive,semantic}`, `--rebuild` (drop and recreate the collection). Sequence: Stage 1 → 2 → 3 → 4, aborting if the Stage 2 audit or the Stage 3 truncation assertion fails. Write `data/processed/documents.jsonl`, `data/processed/chunks.jsonl`, and `logs/ingest_run.json`. Print a summary table: chunks per scheme, collection count, duration.
7. A startup guard: if the collection is empty, raise `RuntimeError` with the message `Run: python scripts/ingest.py`.

### Acceptance

```bash
python scripts/ingest.py
```

Expected: a summary table with 5 scheme rows, total 152, and no audit or assertion errors. `logs/ingest_run.json` contains the `StrategyDecision`, params, per-scheme counts, and audit result.

**Idempotency:**

```bash
python scripts/ingest.py >/dev/null && python -c "
from src.config import load_config
from src.pipeline.stage4_store import get_client, get_or_create_collection
c = load_config(); col = get_or_create_collection(get_client(c), c)
print('count_after_two_ingests', col.count())
"
```

Expected: the same number as after a single ingest — **not** doubled.

**Retrieval smoke test** — the real test of Stage 4:

```bash
python -c "
from src.config import load_config
from src.pipeline.stage4_store import query_collection
c = load_config()
r = query_collection(c, 'What is the minimum SIP?', scheme_ids=['hdfc_elss'], n_results=3)
for m in r['metadatas'][0]: print(m['field'], repr(m['value']))
"
```

Expected top hit: `min_sip '₹500'`

```bash
python -c "
from src.config import load_config
from src.pipeline.stage4_store import query_collection
c = load_config()
r = query_collection(c, 'expense ratio', scheme_ids=['hdfc_flexi_cap'], n_results=3)
for m in r['metadatas'][0]: print(m['scheme_id'], m['field'], repr(m['value']))
"
```

Expected: **every** result has `scheme_id == 'hdfc_flexi_cap'` (proves the filter works) and the top hit is `expense_ratio '0.77%'`.

### Traps

- **`None` in Chroma metadata raises.** `effective_date: None` must become `""` or be omitted. This is the single most common Stage 4 crash.
- Use `upsert`, not `add`, or the second ingest duplicates everything.
- The `where` filter with `$in` needs the list form; `{"scheme_id": "x"}` is the single-value form. Do not mix.
- `n_results` must not exceed the collection count, or Chroma warns/errors. Guard it.
- **Pass `embeddings=` explicitly to `upsert`.** If you pass only `ids`/`documents`/`metadatas`, Chroma silently embeds `chunk.text` with its own bundled ONNX MiniLM instead of using the Stage 3 vectors. It still *works* — same model family, and retrieval spot checks pass — which is what makes it dangerous: the Phase 2 `embed_input` prefix (scheme name + category) is absent from the index, `data/chunks.embeddings.npz` is dead weight, and the trace claims Stage 3 vectors that were never stored. Symptom to watch for: a 79 MB `onnx_models/all-MiniLM-L6-v2` download. `store_chunks` therefore **requires** `vectors` and calls `assert_stored_vectors` after upserting; `scripts/verify_pipeline.py` cross-checks the store against a fresh Stage 3 recompute. Verified delta after the fix: `7.45e-09` (float32 round-trip) versus `8.51e-02` when Chroma substitutes its own model.

### Prompt

> Read `architecture.md` §7.4 and `implementation.md` §8. Execute **Phase 4** only. Create `src/pipeline/stage4_store.py` and `scripts/ingest.py`. Critical: Chroma metadata accepts only `str`/`int`/`float`/`bool`, so coerce or omit every `None` (especially `effective_date`) — passing `None` will crash. Use `collection.upsert` with deterministic `chunk.chunk_id` so re-ingesting twice does not double the collection; prove that with the idempotency check. Use cosine HNSW space. Build the scheme filter as `{"scheme_id": {"$in": [...]}}`. `scripts/ingest.py` must run Stages 1→4 in order and abort on a failed Stage 2 audit or Stage 3 truncation assertion. Run all four acceptance commands and paste the real output, including the `min_sip '₹500'` smoke test for ELSS and the scheme-filter test for Flexi Cap.

---

## 9. Phase 5 — Stage 5: Retrieve + Guards

**Goal:** PII, advice, and performance questions are blocked *before* the vector store; scheme aliases resolve; MMR reranking works.

**Depends on:** Phase 4.

### Files

```
src/guards/__init__.py
src/guards/pii.py
src/guards/intent.py
src/guards/performance.py
src/retrieval/aliases.py
src/retrieval/vector_cache.py
src/retrieval/rerank.py
src/pipeline/stage5_retrieve.py
```

#### 7.1 Deviations from the Phase 5 spec

All four are deliberate, verified against the eval sets, and recorded here rather than
left implicit in the code.

**7.1.1 MMR extended with a field cap and value de-duplication.**
`max_per_field=3` plus "select a `(field, value)` pair at most once". Pure MMR
fails the unfiltered question *"What is the minimum SIP?"*: the vector search
correctly returns all 5 `min_sip` chunks, but four of them read `₹100` and are
mutually redundant, so MMR demoted ELSS `₹500` — the only distinctive answer — to
promote a weaker match. Measured before the fix: `flexi/small/large/balanced` all
`₹100`, ELSS absent. After: `₹100` once, ELSS `₹500` retained. The cap is 3 and not
1 so a 5-scheme question can still surface 3 distinct values (*"expense ratio"*
returns `1.03% / 0.78% / 0.77%`).

**7.1.2 MMR drops superseded chunks.** `exit_load_historical` rows are removed from
the pool whenever a current chunk for the same field is present, compared on the
*base* field (`exit_load_historical → exit_load`, since the historical rows carry
their own field name and a naive comparison never matches). Without this, golden
query g8 retrieved three historical exit-load rows at 0.896/0.892/0.882 similarity
ahead of the current row at 0.855 — precisely the stale-answer failure T4 warns
about. This is the correctness half of the fix; the prompt tag is the other half.
Post-MMR, no golden query surfaces a historical chunk.

**7.1.3 Layer 2 is an independent structural classifier, offline or not.** The
spec's "block only when the lexicon and the classifier agree" is unenforceable if
layer 2 re-calls layer 1 — the first draft's `_heuristic_classify` called
`detect_advice`, so the two layers could only ever agree and the code had to
special-case `llm is None` to "block on layer 1 alone". Replaced with
`_offline_classify`, which reads grammar and query shape (decision modal + action
verb, superlative over a set, return-projection frame) and never consults
`ADVICE_LEXICON`/`PERFORMANCE_LEXICON`. The two layers can now genuinely disagree,
in both the LLM and offline paths. Measured effect: *"What should I know about the
exit load?"* trips the `what should i` lexicon entry but is a factual question, and
is now correctly **not** refused; *"Which of these funds has a lock-in period?"*
likewise. All 5 refusal cases still block, and 10/10 factual spot-check queries
pass through.

**7.1.4 `REFUSAL_PERFORMANCE` names the factsheet instead of linking it.** S3/FR9
ask for a link to the official factsheet, but S4/S6 constrain every citation to the
5 allowlisted URLs and the corpus contains no factsheet URL — only the 5 Groww
scheme pages. Linking an `hdfcmf.com` factsheet would be a fabricated citation, so
the refusal names the official factsheet as the place to look and cites the
educational page. Both refusals also now end with the **bare URL and no trailing
period**: a whitespace-splitting citation extractor would otherwise capture
`https://groww.in/mutual-funds.` and S6 would reject it as off-allowlist.
**Contract for Phase 6:** allowlist enforcement applies to factual citations in
answers, not to refusal boilerplate — `https://groww.in/mutual-funds` is
intentionally outside the 5-URL scheme allowlist, and the validator must not
discard a refusal because of it.

### Tasks

1. `guards/pii.py` — `detect_pii(text) -> PIIHit | None` covering: PAN `[A-Z]{5}[0-9]{4}[A-Z]`, Aadhaar (12 digits, tolerating spaces and a preceding `Aadhaar` label), folio/account numbers, 4–6 digit OTPs, emails, and phone numbers. Return the matched span **for in-memory refusal only**. Never log it, never pass it downstream.
2. `guards/pii.py` — `REFUSAL_PII` fixed string, e.g. *"I don't accept or store personal or account identifiers such as PAN, Aadhaar, folio numbers, or OTPs. Please remove it from your question. For account-specific help, contact HDFC Mutual Fund directly."*
3. `guards/intent.py` — `ADVICE_LEXICON` of phrases (`should i buy`, `should i sell`, `which is better`, `is it a good time`, `recommend`, `suggest`, `worth it`, `best fund for`, `build me a portfolio`, `is now a good time`, `exit or hold`). `detect_advice(text) -> bool` by case-insensitive substring and regex. `REFUSAL_ADVICE` = polite facts-only message + one educational link, per `PRD.md` §6.4.
4. `guards/intent.py` — `classify_intent_zeroshot(text) -> str` using the LLM adapter, returning `advice | performance | factual | out_of_scope`. Layer 2 catches paraphrases the lexicon misses. Block only when the lexicon **and** the classifier agree (architecture §7.5.2).
5. `guards/intent.py` — `detect_out_of_corpus(text, config) -> bool` for any AMC other than HDFC, or a scheme that is not one of the 5. Refusal lists the 5 in-scope schemes.
6. `guards/performance.py` — `PERFORMANCE_LEXICON` (`will it return`, `expected return`, `projected`, `which performed better`, `compare returns`, `X% return`, `vs` near return words, `outperform`, `top performing`). `REFUSAL_PERFORMANCE` declines to compute or compare and links the official factsheet, per S3.
7. `retrieval/aliases.py` — `SCHEME_ALIASES` exactly as `architecture.md` §7.5.3, including the load-bearing `equity fund → hdfc_flexi_cap` (T1). `resolve_scheme(text, config) -> SchemeResolution` with `scheme_ids`, `matched_alias`, `confidence`, `ambiguous`. Unmatched → `scheme_ids=[]`, `ambiguous=True`.
8. `retrieval/rerank.py` — `mmr_rerank(query_vec, candidates, k, lambda_)` per the formula in `architecture.md` §7.5.4, λ from `config.retrieve.mmr_lambda`.
9. `pipeline/stage5_retrieve.py` — `retrieve(query, config, embedder) -> RetrievalOutcome` executing the ten ordered steps of `architecture.md` §7.5.1: normalise → PII → advice → performance → out-of-corpus → resolve → search `top_k * oversample_factor` → MMR to 4 → `sim_floor` gate → build the prompt context block. Early-returns a refusal on any guard. The `sim_floor` gate returns an "ungrounded" outcome **without calling the LLM**.
10. `RetrievalOutcome` model: `refused`, `refusal_type`, `guard_decisions[]`, `scheme_resolution`, `candidates[]`, `selected[]`, `grounded` (bool), `prompt_context` (str).

### Acceptance

```bash
python -c "
import json
from src.pipeline.stage5_retrieve import retrieve
from src.config import load_config
c = load_config()
for case in json.load(open('eval/pii_cases.json')):
    r = retrieve(case['text'], c, None)
    print(case['id'], case['label'], r.refused, r.refusal_type)
"
```

Expected: all 5 `True pii`

```bash
python -c "
import json
from src.pipeline.stage5_retrieve import retrieve
from src.config import load_config
c = load_config()
for case in json.load(open('eval/refusal_qa.json')):
    r = retrieve(case['query'], c, None)
    print(case['id'], r.refused, r.refusal_type)
"
```

Expected: all 5 `True`, with `refusal_type` matching `expected_refusal_type`.

```bash
python -c "
from src.retrieval.aliases import resolve_scheme
from src.config import load_config
c = load_config()
for q in ['HDFC equity fund', 'flexi cap', 'tax saver 80c', 'small cap', 'HDFC Parag Parikh Flexi Cap']:
    print(q, '->', resolve_scheme(q, c).scheme_ids, resolve_scheme(q, c).ambiguous)
"
```

Expected:
```
HDFC equity fund -> ['hdfc_flexi_cap'] False
flexi cap -> ['hdfc_flexi_cap'] False
tax saver 80c -> ['hdfc_elss'] False
small cap -> ['hdfc_small_cap'] False
HDFC Parag Parikh Flexi Cap -> [] True
```

That last line is T1's negative case: "flexi cap" in another AMC's name must not resolve to HDFC.

```bash
python -c "
from src.pipeline.stage5_retrieve import retrieve
from src.config import load_config
c = load_config()
r = retrieve('What is the minimum SIP?', c, None)
print(r.grounded, [ (x.chunk.field, x.chunk.value) for x in r.selected ])
"
```

Expected: `grounded True` and the selected set includes `min_sip`.

### Traps

- PII detection must run **first**, before scheme resolution, retrieval, or any logging. A PII string must never reach Chroma.
- Layer-1-only advice detection leaks on paraphrase; layer-2-only is non-deterministic. Implement both, block on agreement.
- `resolve_scheme` must not match a scheme name qualified by a different AMC (T1 negative case).
- `sim_floor` is 0.30 on cosine distance. Calibrate it in Phase 7 against the golden set; do not tune it now to make a test pass.
- The `evidence` chunk from the sim_floor gate must be excluded from the prompt, not merely flagged.

### Prompt

> Read `architecture.md` §7.5 and `implementation.md` §9. Execute **Phase 5** only. Create the three guard modules, `retrieval/aliases.py`, `retrieval/rerank.py`, and `pipeline/stage5_retrieve.py`. Ordering is the hard requirement: PII detection runs **before** anything touches Chroma, the LLM, or a log, and a PII-bearing query must be discarded from memory without being written anywhere. Implement two-layer advice detection (phrase lexicon AND a zero-shot classifier, blocking only on agreement). Build `SCHEME_ALIASES` from `architecture.md` §7.5.3 — critically `equity fund → hdfc_flexi_cap` — and make sure a scheme name qualified by a different AMC (`HDFC Parag Parikh Flexi Cap`) resolves to nothing rather than to HDFC's Flexi Cap. Implement MMR manually with λ from config. Run all four acceptance commands and paste the real output, including the 5/5 PII and 5/5 refusal results.

---

## 10. Phase 6 — Stage 6: Generate + Validate + UI

**Goal:** Every answer is ≤3 sentences, grounded in a retrieved chunk, with exactly one allowlisted citation and a `Last updated` line — visible in a Streamlit UI.

**Depends on:** Phase 5.

### Files

```
src/llm/__init__.py
src/llm/client.py
src/guards/validator.py
src/pipeline/stage6_generate.py
src/observability/trace.py
app/streamlit_app.py
```

#### 10.1 Deviations from the Phase 6 spec

**10.1.1 A ninth validator check, `evidence_scheme_match`.** The spec's fault
injection (architecture §9) requires that handing the generator a chunk from a
*different* scheme is rejected. That does **not** work with the eight specified
checks. `grounded` only asserts a token appears in the evidence it is given, and
two HDFC schemes publish the same exit load, so
*"HDFC ELSS exit load is 1% if redeemed within 1 year"* validated `True` against
a **HDFC Large Cap** chunk. The check compares the evidence's own `scheme_id`
against the resolved `scheme_ids`. It skips when the resolution is ambiguous
(cross-scheme questions have no single scheme) and when the chunks carry no
`scheme_id` (plain-text fixtures), so the spec's five acceptance cases are
unchanged. Fault injection now rejects; a correctly-scheme-matched answer still
passes.

**10.1.2 `no_advice` uses both layers, not just the lexicon.** `ADVICE_LEXICON`
has `should i buy` but not `you should buy`, so a generated answer reading
*"HDFC ELSS is the best fund, you should buy it"* passed `no_advice`. The output
check now also applies the structural signals, which catch it. Note this is
still a *deterministic* check on the output: a real LLM classifier is not
consulted on generated text, because `validate()`'s signature is fixed by the
spec. Phase 7 should decide whether the LLM should also vet outputs.

**10.1.3 Provider: Groq, and not a reasoning model.** `.env` holds the key and
is gitignored; the key appears in no source, doc, or config file.
`llama-3.1-8b-instant` is no longer served on this account. `openai/gpt-oss-20b`
and `-120b` are *reasoning* models: they emit a `reasoning` field and burned the
entire `max_tokens` budget without producing `content` (`finish_reason=length`
at both 20 and 200 tokens), returning an empty string that fails validation.
`qwen/qwen3.8-27b` is non-reasoning, ~0.2 s, and correct on the guard
classifier, so it serves both roles. Re-check `/openai/v1/models` before
changing this.

**10.1.4 The fallback classifier fails closed.** `GroqClient.classify_intent`
returns `advice` when the provider errors or the reply does not parse to one of
the four labels, so an unreachable classifier cannot silently downgrade a
refusal. This matters: the two-layer guard blocks only on *agreement*, so a
layer 2 that returned `factual` for a performance question would have let r3 and
r5 through.

**10.1.5 Model choice is a Phase 7 risk, not a Phase 6 one.** The prompt is the
only defence against advice, forecasts and paraphrase; with `FakeLLM` the
validator is fully tested, but a real model's compliance is not. All 5
refusals, 5 PII cases and 10/10 golden grounding hold with the live LLM as
layer 2, but that is prompt-dependent and not yet a test.

**10.1.6 Trace stages 1–3 come from the ingest log.** Fetching, chunking and
embedding happen once, offline, so they cannot be re-derived per question. The
UI shows the recorded run rather than implying they were recomputed. Stage 4 is
read live from the running collection; 5 and 6 are built for this question. The
trace reports all 6 stages.

### Tasks

1. `llm/client.py` — `class LLMClient` with `chat(system: str, user: str) -> str`, `temperature=0.0`, `max_tokens` from config, and a provider adapter selected by env var (`LLM_PROVIDER`). Include a `FakeLLM` that returns a canned string, so every downstream test runs with **no API key and no network**.
2. `llm/client.py` — `SYSTEM_PROMPT` as a module constant, reproducing `PRD.md` §6.5 verbatim, plus two explicit rules: fund AUM comes from `Fund size (AUM)` (the About block is AMC-level), and historical exit-load rows are not the current load. Inject `fetched_at` and the allowlist at call time — never hardcode.
3. `guards/validator.py` — `validate(answer_text, context_chunks, query, scheme_resolution, config) -> ValidationResult` running all eight checks from `architecture.md` §7.6.1: `citation_present`, `citation_allowed` (reuse `assert_allowed` from Phase 1), `grounded`, `scheme_named`, `no_advice`, `no_pii`, `sentence_budget`, `has_last_updated`.
4. **`grounded` is the load-bearing check.** Extract every numeric, currency, percentage, and date token from the answer; require each to appear **verbatim** in the concatenation of the retrieved chunks. Ignore tokens that came from the query itself.
5. `pipeline/stage6_generate.py` — `answer(query, config, llm) -> Answer`. Early-return a refusal if Stage 5 refused or was ungrounded. Otherwise build the prompt, call the LLM, validate; on failure retry once with stricter instructions; after that return the safe fallback plus the source link. **Never return an unvalidated answer.**
6. `observability/trace.py` — `Trace` builder populating `stage1`…`stage6` as the answer is produced (architecture §10): source URL, `fetched_at`, `text_sha256`; `chunk_id`s, `token_count`s, `StrategyDecision`; model id, dim, truncation flag; collection name and count; `GuardDecision[]`, `SchemeResolution`, top-4 `(chunk_id, cosine)`; final text, citation, `ValidationResult`, retry count.
7. `app/streamlit_app.py` — `st.set_page_config`; welcome line; the standing note **"Facts-only. No investment advice."**; 3 example questions (expense ratio of HDFC Small Cap, exit load on HDFC ELSS, minimum SIP for HDFC Flexi Cap) as clickable buttons; a text input; and per-answer rendering of answer text → **one** citation link → `Last updated from sources: <date>`. Add a `st.expander("How this was answered")` rendering the `Trace` as JSON.
8. Embed the **full disclaimer** from `PRD.md` §8.1 verbatim in the sidebar, unmodified.
9. Guard: if the collection is empty, show `Run: python scripts/ingest.py` instead of a traceback.
10. `app/streamlit_app.py` — no `st.session_state` persistence of queries to disk; no cookies; no analytics.

### Acceptance

With `FakeLLM` (no key, no network):

```bash
python -c "
from src.pipeline.stage6_generate import answer
from src.config import load_config
from src.llm.client import FakeLLM
c = load_config()
a = answer('What is the minimum SIP for HDFC ELSS?', c, FakeLLM(canned='HDFC ELSS Tax Saver Fund Direct Plan Growth has a minimum SIP of ₹500. Source: https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth\nLast updated from sources: 2026-09-27'))
print(a.validation.passed, a.citation_url, a.last_updated)
"
```

Expected: `True https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth 2026-09-27`

**Validator unit checks** — each must fail independently:

```bash
python -c "
from src.guards.validator import validate
from src.config import load_config
c = load_config()
ok = 'HDFC ELSS minimum SIP is ₹500. Source: https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth\nLast updated from sources: 2026-09-27'
ctx = [{'text': 'HDFC ELSS Tax Saver Fund Direct Plan Growth - Minimum investments. Min. for SIP: ₹500.'}]
from src.retrieval.aliases import SchemeResolution
r = SchemeResolution(scheme_ids=['hdfc_elss'], matched_alias='elss', confidence=1.0, ambiguous=False)
print('good      ', validate(ok, ctx, 'q', r, c).passed)
print('no_cite   ', validate(ok.replace('https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth',''), ctx, 'q', r, c).passed)
print('ungrounded', validate(ok.replace('₹500','₹999'), ctx, 'q', r, c).passed)
print('bad_cite  ', validate(ok.replace('groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth','example.com/fake'), ctx, 'q', r, c).passed)
print('too_long  ', validate(ok + ' ' + 'Extra sentence here. ' * 5, ctx, 'q', r, c).passed)
"
```

Expected: `good True`, and `False` for all four failure cases.

With a real LLM key set:

```bash
export LLM_API_KEY=...
streamlit run app/streamlit_app.py
```

Manually confirm: clicking an example question returns a ≤3-sentence answer with exactly one working citation and a `Last updated` line; "How this was answered" shows all 6 stages; the sidebar disclaimer matches §8.1 word for word.

### Traps

- Develop and test with `FakeLLM` first. Every validator test must pass without an API key.
- The `grounded` check must strip tokens that appeared in the **user's query**, or echoing the query's own number would false-positive as ungrounded.
- `sentence_budget` must use real sentence segmentation, not `.count('.')` — "1.03%" contains a period.
- Never let an unvalidated answer reach the UI, even after the retry is exhausted.
- The API key comes from the environment. Never write it into a file, a log, or the `Trace`.

### Prompt

> Read `architecture.md` §7.6 and §10, and `PRD.md` §6.5, §7, §8.1. Execute **Phase 6** only. Create the LLM adapter with a `FakeLLM`, the post-validator, `pipeline/stage6_generate.py`, `observability/trace.py`, and `app/streamlit_app.py`. The post-validator's `grounded` check is the most important part: extract every numeric, currency, percentage and date token from the answer and require each to appear verbatim in the retrieved chunks — but ignore tokens that came from the user's own query, or the check will false-positive. Implement sentence counting with real segmentation, not `count('.')`, because values like `1.03%` contain periods. An answer that fails validation twice must return the safe fallback plus the source link, never the unvalidated text. Build the Streamlit UI with the welcome line, 3 example questions, the note "Facts-only. No investment advice.", the full §8.1 disclaimer verbatim in the sidebar, one citation per answer, the `Last updated from sources:` line, and the "How this was answered" trace expander. First run the validator acceptance checks with `FakeLLM` and no API key; paste the real output showing `good True` and all four negative cases `False`.

---

## 11. Phase 7 — Validation & Evaluation

**Goal:** The full test suite passes and the eval report shows ≥9/10 golden, 5/5 refusals, 0 PII persisted, and both fault-injection cases caught.

**Depends on:** Phase 6.

### Files

```
tests/test_stage1_load.py
tests/test_stage2_chunk.py
tests/test_stage3_embed.py
tests/test_stage4_store.py
tests/test_guards.py
tests/test_aliases.py
tests/test_validator.py
tests/test_pipeline_integration.py
scripts/evaluate.py
logs/eval_results.json
```

### Tasks

1. Implement every suite in `architecture.md` §9. The Stage 2 test is the most important: assert all 20 gold facts from §3 are extracted with their **exact** expected values.
2. **Fault injection A (required).** Call `answer()` with the `min_sip` chunk from ELSS (₹500) while the query asks about Large Cap (correct answer ₹100). `ValidationResult.passed` must be `False` with `grounded` among the failures.
3. **Fault injection B (required).** Supply the `amc_aum_total` chunk when the question asks fund AUM for HDFC Large Cap (₹39,933.37 Cr). Must be rejected, or explicitly re-scoped as AMC-level — never silently accepted.
4. `scripts/evaluate.py` — run all three suites and write `logs/eval_results.json`:
   - `golden`: 10 queries from `eval/golden_qa.json`. For each, `expected_value` must appear in the answer, the citation must equal `expected_source_url`, and the answer must be ≤3 sentences. Record `pass`, `got_value`, `citation`, `sentences`.
   - `refusal`: 5 queries. Assert `refused is True` and `refusal_type` matches. Additionally assert no answer text contains a recommendation lexicon token.
   - `pii`: 5 strings. Assert `refused is True` and `refusal_type == 'pii'`. **Then assert that no PII substring appears in `logs/eval_results.json` or any log file** — read the logs back from disk and grep.
   - Also compute a `grounded_rate` and the mean top-1 cosine distance, to calibrate `retrieve.sim_floor`.
5. Exit non-zero if any suite is below its threshold, so `evaluate.py` is CI-usable.

### Acceptance

```bash
pytest -q
```

Expected: **all tests pass**, no skips. Any skip means the corresponding feature was not implemented — fix it, do not skip.

```bash
python scripts/evaluate.py
```

Expected `logs/eval_results.json`:

```json
{
  "golden":  {"passed": 10, "total": 10},
  "refusal": {"passed": 5, "total": 5},
  "pii":     {"passed": 5, "total": 5, "pii_found_in_logs": false},
  "grounded_rate": 1.0
}
```

And a printed **Fault injection** section showing `A: caught` and `B: caught`.

Manual spot-check of the 10 golden answers against §3 — every value must match exactly. A single mismatch is a bug, not a rounding issue.

### Traps

- The 10 golden queries are the whole visible test surface. Do not tune the prompt, the chunker, or `sim_floor` until they pass — fix the actual defect. Overfitting to 10 queries is the main way this project gets a misleading grade.
- The PII log-scan is a *file* check, not a memory check. Reading the log back from disk is the only version that proves anything.
- `pytest` must not require an API key. Use `FakeLLM` everywhere except one explicitly-marked live test.

### Prompt

> Read `architecture.md` §9, §11, and `implementation.md` §3. Execute **Phase 7** only. Write the full test suite from `architecture.md` §9 — every suite, no skips, and none of them requiring an API key (use `FakeLLM`). The Stage 2 test is the priority: assert that all 20 gold facts in `implementation.md` §3 are extracted with their exact expected values. Then implement the two required fault-injection cases: (A) hand the generator ELSS's `min_sip` chunk (₹500) for a question about Large Cap and assert the `grounded` check rejects it; (B) hand it the AMC-level `Total AUM` chunk for a fund-AUM question and assert it is rejected or explicitly re-scoped. Write `scripts/evaluate.py` to run the 10 golden, 5 refusal and 5 PII cases, write `logs/eval_results.json`, and exit non-zero on failure. The PII check must read the log files back **from disk** and assert no PII substring is present anywhere. Do not tune the prompt or `sim_floor` to make tests pass — fix the underlying defect. Run `pytest -q` and `python scripts/evaluate.py` and paste the real output.

---

## 12. Phase 8 — Docs & Deliverables

**Goal:** Every item in `PRD.md` §12 exists and is accurate.

**Depends on:** Phase 7.

### Files

```
README.md
sources.csv
SAMPLE_QA.md
```

### Tasks

1. `sources.csv` — header `scheme_id,category,scheme_name,url,retrieved_at,plan`, then the 5 rows from §3.2 with `plan` = `Direct Growth` and `retrieved_at` = the real `fetched_at` from the last ingest (read it from `logs/ingest_run.json`; do not invent a date).
2. `README.md` — sections: What it is · Scope (HDFC AMC + the 5 schemes, one AMC only) · Architecture (link `architecture.md`, one paragraph on the 6 stages) · Setup (venv, `pip install -r requirements.txt`, `python scripts/ingest.py`, `streamlit run app/streamlit_app.py`) · The 5 source URLs · Tests (`pytest -q`, `python scripts/evaluate.py`) · **Known limits** (copy from `implementation.md` §13) · Disclaimer.
3. `SAMPLE_QA.md` — 10 queries with the assistant's real answers, each with its citation and `Last updated` line. Copy the actual output of `scripts/evaluate.py`; do not hand-write plausible-looking answers.
4. Include 2 refusal examples (one advice, one performance) showing the polite refusal and the educational link.
5. Record a ≤3-minute demo video **or** deploy the Streamlit app and capture the hosted link.

### Acceptance

- `sources.csv` parses with `csv.DictReader` and has exactly 5 data rows.
- Every URL in `sources.csv` is in the allowlist in `config.yaml`.
- Every `SAMPLE_QA.md` answer appears verbatim in `logs/eval_results.json` and is ≤3 sentences.
- A clean-machine run of the README setup steps succeeds end to end.

### Traps

- Do not hand-write sample answers. They must be real output, or the sample file misrepresents the system.
- Known limits must be honest: 1 AMC, 5 pages, snapshot staleness, link-out for statement downloads, no cross-scheme comparison, machine-extracted text that should be verified on the linked page.
- `fetched_at` in `sources.csv` must be the actual ingest date.

### Prompt

> Read `PRD.md` §12 and `implementation.md` §12–13. Execute **Phase 8** only. Create `sources.csv` (5 rows, real `fetched_at` read from `logs/ingest_run.json`), `README.md`, and `SAMPLE_QA.md`. The `SAMPLE_QA.md` answers must be copied verbatim from `logs/eval_results.json` — do not hand-write them; if the real output is unflattering, fix the system in Phase 7 instead. The README's Known Limits section must state: one AMC only, 5 pages, snapshot staleness, statement-download questions answered by link-out, no cross-scheme comparison, and that answers are machine-extracted and should be verified on the linked page. Verify `sources.csv` parses with `csv.DictReader`, has 5 rows, and that every URL is in the `config.yaml` allowlist. Then produce either a working hosted Streamlit link or record a ≤3-minute demo video.

---

## 13. Final Submission Checklist

Run this before submitting. Every line must be true.

**Functionality**
- [ ] `python scripts/ingest.py` completes; `logs/ingest_run.json` shows the R1–R4 verdict
- [ ] `pytest -q` passes with **no skips**
- [ ] `python scripts/evaluate.py` reports ≥9/10 golden, 5/5 refusals, 5/5 PII, 0 PII in logs
- [ ] Both fault-injection cases caught
- [ ] All 6 stages visible in the "How this was answered" panel

**Constraint compliance**
- [ ] Every answer ≤3 sentences
- [ ] Every answer has exactly one citation, and every cited URL is in the allowlist
- [ ] Every answer shows `Last updated from sources:`
- [ ] 5/5 advice and performance questions refused, no opinion leaked
- [ ] 5/5 PII inputs refused and absent from every file on disk
- [ ] No return computed or compared anywhere
- [ ] Only the 5 allowlisted pages were fetched
- [ ] Disclaimer present in the UI, word for word from `PRD.md` §8.1

**Deliverables**
- [ ] Hosted link or ≤3-min demo video
- [ ] `sources.csv` (5 URLs)
- [ ] `README.md` with setup, scope, known limits
- [ ] `SAMPLE_QA.md` with 5–10 real Q&A + links
- [ ] `PRD.md`, `architecture.md`, `implementation.md` committed

**Honesty**
- [ ] No number in `SAMPLE_QA.md` was written by hand
- [ ] Known limits are stated, not hidden
- [ ] Nothing in the repo is a screenshot of a vendor back-end

---

## 14. Known Limits to Publish

State these plainly in the README. They are design decisions, not defects to hide.

| # | Limit |
|---|---|
| 1 | One AMC, 5 schemes, one plan variant each (Direct Growth). Other AMCs, other plans, and IDCW/Regular are out of scope |
| 2 | Corpus is 5 pages; anything not on them is answered "not in my corpus" rather than guessed |
| 3 | NAV, AUM, and expense ratios change. `fetched_at` reflects ingestion, and the demo runs from a snapshot |
| 4 | Statement-download guidance is a link-out, not a corpus answer (FR12, `architecture.md` A12) |
| 5 | Cross-scheme comparison ("which is cheapest?") is excluded by the no-comparison rule, even though the data would allow it |
| 6 | Semantic chunking is implemented but off by default; the label-aware strategy was chosen by the R1–R4 rules |
| 7 | Answers are machine-extracted text. The grounding validator reduces misattribution but does not eliminate it — verify on the linked page |
| 8 | The eval suite is 10 golden queries. Passing it is evidence, not proof of general correctness |

---

## 15. Appendix A — `config.yaml` (authoritative copy)

Phase 0 must reproduce this exactly. If you change a value here, change it in `architecture.md` §6 too.

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

## 16. Appendix B — System Prompt (authoritative copy)

`llm/client.py` must hold this as `SYSTEM_PROMPT`, with `fetched_at` and the allowlist injected at call time.

```
You are a mutual-fund FACTS assistant. You do not give advice, opinions,
recommendations, or forecasts. You never compute or compare returns.

Rules:
- Answer only from <context>. If a fact is not in the context, say it is not
  available there and link the source page. Never guess, never infer, never use
  outside knowledge.
- Max 3 sentences. Be literal; copy numbers, names and index names verbatim.
- Always name the scheme AND its plan, e.g. "HDFC Flexi Cap Direct Plan Growth".
  Never answer about "the fund" without naming it.
- End with exactly one citation link, chosen from this allowlist and no other:
  {allowlist}
- Then output exactly this line: "Last updated from sources: {fetched_at}"
- Fund-level AUM is the "Fund size (AUM)" field. The "About" block figure
  (Total AUM) is AMC-level and is identical across all five schemes; only use
  it if the user explicitly asks about HDFC AMC, and label it as AMC-level.
- Exit-load rows carrying an effective date are historical unless they are the
  latest effective date. Do not present a historical row as the current load.
- If the question asks for advice or for a return projection or comparison,
  refuse: you are facts-only, and link the official factsheet instead.

<context>
{retrieved_chunks_with_source_urls}
</context>

Question: {user_query}
```

---

## 17. Appendix C — UI Disclaimer (authoritative copy)

Must appear in the Streamlit sidebar **verbatim**, unedited, from `PRD.md` §8.1.

> **Facts-only. No investment advice.** This assistant answers factual questions about 5 HDFC Mutual Fund schemes using public Groww pages. It does not recommend buying, selling, or holding any fund, and it does not compute or compare returns. Mutual fund investments are subject to market risks; read all scheme-related documents carefully. Verify every figure on the linked source page before acting.

---

## 18. Appendix D — Quick Reference

| Need | Look at |
|---|---|
| What the product must do | `PRD.md` |
| How it is built | `architecture.md` |
| What to build now | this file, current Phase |
| Exact expected values | `implementation.md` §3 |
| Known failure modes | `implementation.md` §3.1 (T1–T7) + each Phase's Traps |
| Config values | `implementation.md` §15 / `architecture.md` §6 |
| System prompt text | `implementation.md` §16 / `PRD.md` §6.5 |
| Disclaimer text | `implementation.md` §17 / `PRD.md` §8.1 |
| Pass thresholds | `implementation.md` §11 + `PRD.md` §11 |
| Limits to publish | `implementation.md` §14 |

**Current phase: 0 — Scope Lock & Scaffold.**
