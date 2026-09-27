from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

import requests

from src.config import PROJECT_ROOT, AppConfig, load_config
from src.models import Document, ExtractionError, FetchError, AllowlistViolation

RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
IST = timezone(timedelta(hours=5, minutes=30))
NOISE_BLOCK_PATTERNS = (
    r"Compare similar funds",
    r"Return calculator",
    r"Understand terms",
    r"Check past data",
)
RTA_DISPLAY = "Cams"
FALLBACK_TAX_NOTE = (
    "If you redeem within one year, returns are taxed at 20%. If you redeem "
    "after one year, returns exceeding Rs 1.25 lakh in a financial year are "
    "taxed at 12.5%."
)


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[: -len(":443")]
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[: -len(":80")]
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def url_is_allowed(url: str, config: AppConfig) -> bool:
    try:
        candidate = normalize_url(url)
    except ValueError:
        return False
    return any(
        candidate == normalize_url(entry.url) for entry in config.sources.allowlist
    )


def assert_allowed(url: str, config: AppConfig) -> None:
    if not url_is_allowed(url, config):
        raise AllowlistViolation(f"url not in allowlist: {url}")


def fetch(
    url: str,
    config: AppConfig,
    session: Optional[requests.Session] = None,
) -> str:
    assert_allowed(url, config)
    owned = session is None
    active = session or requests.Session()
    headers = {"User-Agent": config.sources.user_agent}
    last_error: Optional[Exception] = None
    try:
        for attempt in range(config.sources.max_retries):
            try:
                response = active.get(
                    url, headers=headers, timeout=config.sources.timeout_s
                )
                response.raise_for_status()
                return response.text
            except Exception as exc:
                last_error = exc
                if attempt + 1 < config.sources.max_retries:
                    time.sleep(2 ** attempt)
    finally:
        if owned:
            active.close()
    raise FetchError(f"failed to fetch {url}: {last_error}")


def extract_structured(html: str) -> Optional[Dict[str, Any]]:
    match = NEXT_DATA_RE.search(html)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
        data = payload["props"]["pageProps"]["mfServerSideData"]
    except (ValueError, KeyError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def extract_main_text(html: str, config: AppConfig) -> str:
    try:
        import trafilatura
    except ImportError:
        trafilatura = None
    if trafilatura is not None:
        text = trafilatura.extract(
            html,
            include_tables=False,
            include_comments=False,
            favor_recall=True,
        )
        if text and len(text.strip()) >= config.load.min_text_chars:
            return text
    text = extract_with_soup(html, config)
    if len(text.strip()) < config.load.min_text_chars:
        raise ExtractionError(
            f"extracted {len(text.strip())} chars, "
            f"minimum is {config.load.min_text_chars}"
        )
    return text


def extract_with_soup(html: str, config: AppConfig) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for selector in config.load.drop_selectors:
        try:
            for node in soup.select(selector):
                node.decompose()
        except Exception:
            continue
    target = soup.find("main")
    if target is None:
        candidates = soup.find_all("div")
        target = max(candidates, key=lambda n: len(n.get_text()), default=soup)
    return target.get_text(separator="\n", strip=True)


def strip_noise(text: str, config: AppConfig) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern in NOISE_BLOCK_PATTERNS:
        cleaned = re.sub(
            r"{0}(.{{0,400}}?)(?=\n{{2,}}|\Z)".format(re.escape(pattern)),
            "",
            cleaned,
            flags=re.DOTALL,
        )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def normalize(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("\u2013", "-").replace("\u2014", "-")
    cleaned = cleaned.replace("\u2019", "'").replace("\u00a0", " ")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def format_inr(value: Any, suffix: str = "", decimals: int = 2) -> str:
    if value is None:
        return "Not available"
    number = float(value)
    quantised = f"{abs(number):.{decimals}f}"
    if decimals > 0 and "." in quantised:
        whole, _, fraction = quantised.partition(".")
        fraction = fraction.rstrip("0")
    else:
        whole, fraction = quantised, ""
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = [tail]
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups)
    sign = "-" if number < 0 else ""
    rendered = f"{sign}{whole}" + (f".{fraction}" if fraction else "")
    return f"₹{rendered}{suffix}"


def format_percent(value: Any) -> str:
    if value is None:
        return "Not available"
    text = str(value).strip()
    if text.endswith("%"):
        return text
    return f"{text}%"


def format_date(value: Any) -> str:
    if value is None:
        return "Not available"
    text = str(value).strip()
    if re.fullmatch(r"\d{2}-[A-Z][a-z]{2}-\d{4}", text):
        return text
    try:
        parsed = to_ist(text)
    except ValueError:
        return text
    return parsed.strftime("%d %b %Y")


def format_tenure(value: Any) -> str:
    if not value:
        return "Not available"
    try:
        parsed = to_ist(str(value))
    except ValueError:
        return str(value)
    return parsed.strftime("%b %Y")


def format_lock_in(lock_in: Any) -> str:
    if not isinstance(lock_in, dict):
        return "None"
    years = lock_in.get("years")
    months = lock_in.get("months")
    days = lock_in.get("days")
    if not years and not months and not days:
        return "None"
    parts = []
    if years:
        parts.append(f"{int(years)} year" + ("s" if int(years) != 1 else ""))
    if months:
        parts.append(f"{int(months)} month" + ("s" if int(months) != 1 else ""))
    if days:
        parts.append(f"{int(days)} day" + ("s" if int(days) != 1 else ""))
    return " ".join(parts)


def clean(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r", "").replace("\n", " ").strip()
    return value


def to_ist(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(IST)


def clean_exit_load(value: Any) -> str:
    text = clean(value) or "Not available"
    if text == "Not available":
        return text
    stripped = re.sub(
        r"(?i)^exit\s*load\s*(of\s*)?(for\s*units\s*in\s*)?[:\-]?\s*", "", text
    ).strip()
    return stripped or text


def build_facts(scheme_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    stp = data.get("stp_details") or {}
    rta = data.get("rta_details") or {}
    amc_info = data.get("amc_info") or {}
    category_info = data.get("category_info") or {}
    category = clean(data.get("category")) or ""
    sub_category = clean(data.get("sub_category")) or ""
    display_category = f"{category} {sub_category}".strip()

    risk = None
    for entry in data.get("return_stats") or []:
        if isinstance(entry, dict) and entry.get("risk"):
            risk = clean(entry["risk"])
            break

    historic = []
    for entry in data.get("historic_exit_loads") or []:
        if not isinstance(entry, dict):
            continue
        historic.append(
            {
                "as_on_date": clean(entry.get("as_on_date")),
                "note": clean_exit_load(entry.get("note")),
            }
        )
    historic.sort(key=lambda item: item["as_on_date"] or "", reverse=True)

    managers = []
    for entry in data.get("fund_manager_details") or []:
        if not isinstance(entry, dict):
            continue
        name = clean(entry.get("person_name"))
        if not name:
            continue
        managers.append(
            {
                "name": name,
                "tenure_from": format_tenure(entry.get("date_from")),
                "education": clean(entry.get("education")),
            }
        )

    sector_totals: Dict[str, float] = {}
    equity_rows = []
    for entry in data.get("holdings") or []:
        if not isinstance(entry, dict):
            continue
        weight = entry.get("corpus_per")
        sector = clean(entry.get("sector_name")) or "Unspecified"
        if weight is not None:
            sector_totals[sector] = sector_totals.get(sector, 0.0) + float(weight)
        equity_rows.append(
            {
                "company": clean(entry.get("company_name")),
                "sector": sector,
                "instrument": clean(entry.get("instrument_name")) or clean(
                    entry.get("nature_name")
                ),
                "weight_pct": weight,
            }
        )
    equity_rows.sort(
        key=lambda row: row["weight_pct"] if row["weight_pct"] is not None else -1,
        reverse=True,
    )
    sectors = [
        {"sector": name, "weight_pct": round(weight, 2)}
        for name, weight in sorted(
            sector_totals.items(), key=lambda kv: kv[1], reverse=True
        )
    ]
    portfolio_date = None
    for entry in data.get("holdings") or []:
        if isinstance(entry, dict) and entry.get("portfolio_date"):
            portfolio_date = format_date(entry["portfolio_date"])
            break

    return {
        "scheme_id": scheme_id,
        "scheme_name": clean(data.get("scheme_name")),
        "plan_type": clean(data.get("plan_type")) or "Direct",
        "category": display_category,
        "category_raw": category,
        "sub_category": sub_category,
        "sub_sub_category": clean(data.get("sub_sub_category")),
        "fund_house": clean(data.get("fund_house")) or clean(amc_info.get("name")),
        "nav": format_inr(data.get("nav")),
        "nav_date": format_date(data.get("nav_date")),
        "fund_aum": format_inr(data.get("aum"), " Cr"),
        "amc_aum_total": format_inr(amc_info.get("aum"), " Cr"),
        "amc_rank": clean(amc_info.get("rank")),
        "expense_ratio": format_percent(data.get("expense_ratio")),
        "groww_rating": clean(data.get("groww_rating")),
        "risk_rating": risk or "Not available",
        "min_first_inv": format_inr(data.get("min_investment_amount")),
        "min_additional_inv": format_inr(data.get("mini_additional_investment")),
        "min_sip": format_inr(data.get("min_sip_investment")),
        "sip_multiplier": format_inr(data.get("sip_multiplier")),
        "purchase_multiplier": format_inr(data.get("purchase_multiplier")),
        "min_stp_in": format_inr(stp.get("stp_in_minimum_installment_amount")),
        "exit_load": clean_exit_load(data.get("exit_load")),
        "exit_load_history": historic,
        "stamp_duty": clean(data.get("stamp_duty")) or "Not available",
        "lock_in": format_lock_in(data.get("lock_in")),
        "benchmark": clean(data.get("benchmark_name")) or "Not available",
        "benchmark_short": clean(data.get("benchmark")) or "Not available",
        "scheme_launch_date": format_date(data.get("launch_date")),
        "amc_incorporation_date": format_date(amc_info.get("launch_date")),
        "amc_name": clean(amc_info.get("name")),
        "custodian": clean(rta.get("custodian_name")) or "Not available",
        "rta": clean(rta.get("rta_name")) or RTA_DISPLAY,
        "investment_objective": clean(data.get("description"))
        or "Not available",
        "category_helper_text": clean(category_info.get("category_helper_text"))
        or "Not available",
        "tax_note": clean(category_info.get("tax_impact")) or FALLBACK_TAX_NOTE,
        "holdings_count": len(equity_rows),
        "holdings_portfolio_date": portfolio_date,
        "top_holdings": equity_rows[:5],
        "sector_allocation": sectors,
        "fund_managers": managers,
        "nfo_risk": clean(data.get("nfo_risk")) or "Not available",
    }


def render_facts_text(scheme_id: str, facts: Dict[str, Any], url: str) -> str:
    name = facts["scheme_name"] or scheme_id
    lines: List[str] = [
        name,
        "",
        f"Fund category: {facts['category']}",
        f"Fund house: {facts['fund_house']}",
        f"Plan: {facts['plan_type']} Growth",
        f"Source page: {url}",
        "",
        "Overview",
        f"NAV: {facts['nav']} (as on {facts['nav_date']})",
        f"Fund size (AUM): {facts['fund_aum']}",
        f"Expense ratio: {facts['expense_ratio']}",
        f"Rating: {facts['groww_rating']} out of 5 (Groww rating)",
        f"Risk rating: {facts['risk_rating']} (Groww riskometer)",
        f"Fund benchmark: {facts['benchmark']}",
        f"Scheme launch date: {facts['scheme_launch_date']}",
        f"Date of Incorporation of {facts['amc_name']}: "
        f"{facts['amc_incorporation_date']}",
        f"Total AUM of {facts['amc_name']} (AMC level, not this fund): "
        f"{facts['amc_aum_total']}",
        "",
        "Minimum investments",
        f"Min. for 1st investment: {facts['min_first_inv']}",
        f"Min. for 2nd investment: {facts['min_additional_inv']}",
        f"Min. for SIP: {facts['min_sip']}",
        f"Purchase multiplier: {facts['purchase_multiplier']}",
        "",
        "Exit load, stamp duty and tax",
        f"Exit load: {facts['exit_load']}",
    ]
    for row in facts["exit_load_history"]:
        marker = "current" if row is facts["exit_load_history"][0] else "historical"
        lines.append(
            f"Exit load effective {format_date(row['as_on_date'])} "
            f"({marker}): {row['note']}"
        )
    lines.extend(
        [
            f"Stamp duty on investment: {facts['stamp_duty']}",
            f"Tax implication: {facts['tax_note']}",
            f"Lock-in period: {facts['lock_in']}",
            "",
            "Fund management",
        ]
    )
    if facts["fund_managers"]:
        for entry in facts["fund_managers"]:
            lines.append(
                f"Fund manager: {entry['name']} (from {entry['tenure_from']})"
            )
            if entry["education"]:
                lines.append(f"{entry['name']} education: {entry['education']}")
    else:
        lines.append("Fund manager: Not available")
    lines.extend(
        [
            "",
            "About the scheme",
            f"Investment Objective: {facts['investment_objective']}",
            f"Category description: {facts['category_helper_text']}",
            f"Custodian: {facts['custodian']}",
            f"Registrar & Transfer Agent: {facts['rta']}",
            "",
            "Holdings",
            f"Holdings count: {facts['holdings_count']}",
        ]
    )
    if facts["holdings_portfolio_date"]:
        lines.append(f"Holdings as on: {facts['holdings_portfolio_date']}")
    for entry in facts["top_holdings"]:
        weight = entry["weight_pct"]
        rendered = "Not available" if weight is None else f"{float(weight):.2f}%"
        lines.append(
            f"Top holding: {entry['company']} | sector {entry['sector']} | "
            f"instrument {entry['instrument']} | weight {rendered}"
        )
    for entry in facts["sector_allocation"]:
        lines.append(
            f"Sector allocation: {entry['sector']} | "
            f"weight {float(entry['weight_pct']):.2f}%"
        )
    return normalize("\n".join(lines))


def build_document(
    scheme_id: str,
    scheme_name: str,
    category: str,
    url: str,
    text: str,
    fetched_at: date,
) -> Document:
    return Document(
        doc_id=scheme_id,
        scheme_id=scheme_id,
        scheme_name=scheme_name,
        category=category,
        source_url=url,
        fetched_at=fetched_at,
        text=normalize(text),
        text_sha256=hashlib.sha256(normalize(text).encode("utf-8")).hexdigest(),
    )


def snapshot_paths(scheme_id: str) -> Dict[str, Path]:
    return {
        "text": RAW_DIR / f"{scheme_id}.txt",
        "meta": RAW_DIR / f"{scheme_id}.meta.json",
    }


def _verify_facts(facts: Dict[str, Any]) -> List[str]:
    failures = []
    if not facts.get("scheme_name"):
        failures.append("scheme_name missing")
    for field_name in (
        "expense_ratio",
        "min_sip",
        "benchmark",
        "exit_load",
        "fund_aum",
        "nav",
        "stamp_duty",
        "risk_rating",
    ):
        value = facts.get(field_name)
        if not value or value in {"Not available", ""}:
            failures.append(f"{field_name} missing")
    if not facts.get("custodian") or facts["custodian"] == "Not available":
        failures.append("custodian missing")
    if not facts.get("fund_managers"):
        failures.append("fund_managers missing")
    return failures


def verify_documents(documents: List[Document], facts_by_id: Dict[str, Any]) -> None:
    if len(documents) != len(load_config().sources.allowlist):
        raise ExtractionError(
            f"expected 5 documents, got {len(documents)}"
        )
    seen_ids = [doc.scheme_id for doc in documents]
    if len(set(seen_ids)) != len(seen_ids):
        raise ExtractionError(f"duplicate scheme_id in documents: {seen_ids}")

    problems: List[str] = []
    for doc in documents:
        facts = facts_by_id.get(doc.scheme_id) or {}
        for failure in _verify_facts(facts):
            problems.append(f"{doc.scheme_id}: {failure}")
        for field_name in ("scheme_name", "expense_ratio", "min_sip", "benchmark"):
            needle = str(facts.get(field_name) or "")
            if needle and needle not in doc.text:
                problems.append(f"{doc.scheme_id}: '{needle}' absent from text")
        for marker in ("Compare similar funds", "Return calculator", "Understand terms"):
            if marker in doc.text:
                problems.append(f"{doc.scheme_id}: noise marker '{marker}' present")
    if problems:
        raise ExtractionError("document verification failed: " + "; ".join(problems))


def load_all(
    config: Optional[AppConfig] = None,
    live: bool = False,
    verify: bool = True,
) -> List[Document]:
    config = config or load_config()
    documents: List[Document] = []
    facts_by_id: Dict[str, Any] = {}
    fetched_today = datetime.now().date()
    session = requests.Session() if live else None
    try:
        for entry in config.sources.allowlist:
            paths = snapshot_paths(entry.scheme_id)
            if live or not paths["text"].exists():
                html = fetch(entry.url, config, session)
                data = extract_structured(html)
                if data is not None:
                    facts = build_facts(entry.scheme_id, data)
                    text = render_facts_text(entry.scheme_id, facts, entry.url)
                else:
                    text = normalize(strip_noise(extract_main_text(html, config), config))
                    facts = {
                        "scheme_name": entry.scheme_name,
                        "category": entry.category,
                    }
                if len(text) < config.load.min_text_chars:
                    raise ExtractionError(
                        f"{entry.scheme_id}: text is {len(text)} chars, "
                        f"minimum is {config.load.min_text_chars}"
                    )
                paths["text"].parent.mkdir(parents=True, exist_ok=True)
                paths["text"].write_text(text, encoding="utf-8")
                meta = {
                    "scheme_id": entry.scheme_id,
                    "source_url": entry.url,
                    "fetched_at": fetched_today.isoformat(),
                    "extraction": "next_data" if data is not None else "trafilatura",
                }
                paths["meta"].write_text(
                    json.dumps(meta, indent=2), encoding="utf-8"
                )
                PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
                all_facts_path = PROCESSED_DIR / "facts.json"
                existing = {}
                if all_facts_path.exists():
                    existing = json.loads(all_facts_path.read_text(encoding="utf-8"))
                existing[entry.scheme_id] = facts
                all_facts_path.write_text(
                    json.dumps(existing, indent=2), encoding="utf-8"
                )
                fetched_at = fetched_today
            else:
                text = paths["text"].read_text(encoding="utf-8")
                meta = (
                    json.loads(paths["meta"].read_text(encoding="utf-8"))
                    if paths["meta"].exists()
                    else {}
                )
                raw_date = meta.get("fetched_at")
                fetched_at = (
                    date.fromisoformat(raw_date)
                    if raw_date
                    else datetime.fromtimestamp(
                        paths["text"].stat().st_mtime
                    ).date()
                )
                facts_path = PROCESSED_DIR / "facts.json"
                stored = (
                    json.loads(facts_path.read_text(encoding="utf-8"))
                    if facts_path.exists()
                    else {}
                )
                facts = stored.get(entry.scheme_id) or {
                    "scheme_name": entry.scheme_name,
                    "category": entry.category,
                }
            facts.setdefault("scheme_name", entry.scheme_name)
            facts.setdefault("category", entry.category)
            facts_by_id[entry.scheme_id] = facts
            documents.append(
                build_document(
                    scheme_id=entry.scheme_id,
                    scheme_name=entry.scheme_name,
                    category=entry.category,
                    url=entry.url,
                    text=text,
                    fetched_at=fetched_at,
                )
            )
    finally:
        if session is not None:
            session.close()
    if verify:
        verify_documents(documents, facts_by_id)
    return documents


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed_snapshot", description="Fetch or verify the 5 allowlisted pages"
    )
    parser.add_argument(
        "--live", action="store_true", help="re-fetch instead of using the snapshot"
    )
    parser.add_argument(
        "--no-verify", action="store_true", help="skip the verification oracle"
    )
    args = parser.parse_args(argv)
    try:
        documents = load_all(load_config(), live=args.live, verify=not args.no_verify)
    except (FetchError, ExtractionError, AllowlistViolation) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"mode={'live' if args.live else 'snapshot'} documents={len(documents)}")
    for doc in documents:
        print(
            f"  {doc.scheme_id:26s} chars={len(doc.text):6d} "
            f"fetched_at={doc.fetched_at.isoformat()} sha={doc.text_sha256[:12]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
