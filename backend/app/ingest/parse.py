"""Parses a saved Groww scheme page into clean, fact-level sections.

Primary path: the page's embedded Next.js data (`<script id="__NEXT_DATA__">`), which is
more reliable than the visible text. Fallback: visible page text, flagged so the coverage
report shows the fields as missing (edge case ING-08).

Deliberately EXCLUDED from the corpus (problemStatement §5, content restrictions):
- returns, SIP returns, ratios (Sharpe, beta, std dev), category returns -> performance data
- Groww rating, pros/cons "analysis" -> opinions
- peer comparison -> other AMCs' funds, comparison data
- category_info -> observed to be wrong (labels a mid cap fund "Contra")
- AMC contact phone/email -> not needed; avoids handling contact details
"""

import hashlib
import json
import re
from typing import Any

from app.ingest.normalize import (
    clean_text,
    display_date,
    format_inr,
    format_percent,
    is_missing,
    iso_date,
)

REQUIRED_FIELDS = ("expense_ratio", "exit_load", "min_sip", "benchmark", "riskometer")
TOP_HOLDINGS = 10

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def extract_next_data(html: str) -> dict | None:
    """Return the scheme data object embedded in the page, or None."""
    match = _NEXT_DATA.search(html)
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
        data = payload["props"]["pageProps"]["mfServerSideData"]
    except (ValueError, KeyError, TypeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- field extraction


def _field(value: str | None, as_on: str | None = None, volatile: bool = False) -> dict | None:
    if value is None:
        return None
    return {"value": value, "as_on": as_on, "volatile": volatile}


def _latest(items: list[dict] | None, date_key: str) -> dict | None:
    dated = [i for i in (items or []) if iso_date(i.get(date_key))]
    return max(dated, key=lambda i: iso_date(i[date_key])) if dated else None


def _expense_ratio(d: dict) -> dict | None:
    latest = _latest(d.get("historic_fund_expense"), "as_on_date")
    value = format_percent(d.get("expense_ratio"))
    if value is None and latest:
        value = format_percent(latest.get("expense_ratio"))
    return _field(value, iso_date(latest["as_on_date"]) if latest else None)


def _exit_load(d: dict) -> dict | None:
    text = d.get("exit_load")
    if is_missing(text):
        latest = _latest(d.get("historic_exit_loads"), "as_on_date")
        text = latest.get("note") if latest else None
    if is_missing(text):
        return None
    text = clean_text(text)
    if text.lower() in {"nil", "0", "0%", "0.0%"}:
        text = "Nil (no exit load)"
    return _field(text)


def _riskometer(d: dict) -> dict | None:
    # Current riskometer lives in return_stats[].risk. `nfo_risk` is the level at launch,
    # which can be out of date, so it is not used.
    for stat in d.get("return_stats") or []:
        if not is_missing(stat.get("risk")):
            return _field(clean_text(stat["risk"]))
    match = re.search(r"Risk is ([A-Za-z ]+?)\s*$", clean_text(d.get("meta_desc")))
    return _field(match.group(1)) if match else None


def _lock_in(d: dict) -> dict | None:
    lock = d.get("lock_in") or {}
    parts = [
        f"{int(lock[k])} {k if int(lock[k]) != 1 else k[:-1]}"
        for k in ("years", "months", "days")
        if not is_missing(lock.get(k)) and int(lock[k]) > 0
    ]
    # All-null means the page doesn't state a lock-in; don't infer "no lock-in".
    return _field(", ".join(parts)) if parts else None


def _fund_managers(d: dict) -> dict | None:
    people = []
    for m in d.get("fund_manager_details") or []:
        name = clean_text(m.get("person_name"))
        if not name:
            continue
        since = display_date(iso_date(m.get("date_from")))
        people.append(f"{name} (since {since})" if since else name)
    if not people and not is_missing(d.get("fund_manager")):
        people = [clean_text(d["fund_manager"])]
    return _field(", ".join(people)) if people else None


def _plan(d: dict) -> dict | None:
    plan, option = clean_text(d.get("plan_type")), clean_text(d.get("scheme_type"))
    if not plan and not option:
        return None
    return _field(f"{plan or '?'} plan, {option or '?'} option")


def _category(d: dict) -> dict | None:
    parts = [clean_text(d.get(k)) for k in ("category", "sub_category", "sub_sub_category")]
    parts = [p for p in parts if p]
    return _field(" – ".join(parts)) if parts else None


def _holdings(d: dict) -> dict | None:
    rows = [h for h in d.get("holdings") or [] if h.get("corpus_per") is not None]
    if not rows:
        return None
    rows.sort(key=lambda h: h["corpus_per"], reverse=True)
    as_on = iso_date(rows[0].get("portfolio_date"))
    listed = "; ".join(
        f"{clean_text(h.get('company_name'))} ({format_percent(h['corpus_per'])})"
        for h in rows[:TOP_HOLDINGS]
    )
    return _field(listed, as_on)


def _simple(d: dict, key: str) -> dict | None:
    return None if is_missing(d.get(key)) else _field(clean_text(d[key]))


def extract_fields(d: dict) -> dict[str, dict]:
    nav_date = iso_date(d.get("nav_date"))
    fields = {
        "expense_ratio": _expense_ratio(d),
        "exit_load": _exit_load(d),
        "min_sip": _field(format_inr(d.get("min_sip_investment"))),
        "min_lumpsum": _field(format_inr(d.get("min_investment_amount"))),
        "min_additional_investment": _field(format_inr(d.get("mini_additional_investment"))),
        "benchmark": _simple(d, "benchmark_name") or _simple(d, "benchmark"),
        "riskometer": _riskometer(d),
        "lock_in": _lock_in(d),
        "fund_manager": _fund_managers(d),
        "category": _category(d),
        "plan": _plan(d),
        "fund_house": _simple(d, "fund_house"),
        "registrar": _simple(d, "registrar_agent"),
        "launch_date": _field(display_date(iso_date(d.get("launch_date")))),
        "isin": _simple(d, "isin"),
        "stamp_duty": _simple(d, "stamp_duty"),
        "objective": _simple(d, "description"),
        "aum": _field(format_inr(d.get("aum"), " Cr"), nav_date, volatile=True),
        "nav": _field(format_inr(d.get("nav"), max_decimals=4), nav_date, volatile=True),
        "top_holdings": _holdings(d),
    }
    return {k: v for k, v in fields.items() if v is not None}


# ---------------------------------------------------------------- sections

_TEMPLATES = {
    "expense_ratio": ("Expense ratio", "The expense ratio of {name} is {value}"),
    "exit_load": ("Exit load", "Exit load for {name}: {value}"),
    "min_sip": ("Minimum SIP amount", "The minimum SIP amount for {name} is {value}"),
    "min_lumpsum": ("Minimum lump sum investment",
                    "The minimum lump sum (one-time) investment for {name} is {value}"),
    "min_additional_investment": ("Minimum additional investment",
                                  "The minimum additional investment for {name} is {value}"),
    "benchmark": ("Benchmark index", "The benchmark index of {name} is {value}"),
    "riskometer": ("Riskometer", "The riskometer classification of {name} is {value}"),
    "lock_in": ("Lock-in period", "The lock-in period of {name} is {value}"),
    "fund_manager": ("Fund manager", "{name} is managed by {value}"),
    "category": ("Category", "{name} belongs to the category {value}"),
    "plan": ("Plan and option", "This page covers {name}, {value}"),
    "fund_house": ("Fund house", "{name} is offered by {value}"),
    "registrar": ("Registrar", "The registrar and transfer agent (RTA) for {name} is {value}"),
    "launch_date": ("Launch date", "{name} was launched on {value}"),
    "isin": ("ISIN", "The ISIN of {name} is {value}"),
    "stamp_duty": ("Stamp duty", "Stamp duty applicable to {name}: {value}"),
    "objective": ("Investment objective", "Investment objective of {name}: {value}"),
    "aum": ("Fund size (AUM)", "The fund size (AUM) of {name} is {value}"),
    "nav": ("NAV", "The NAV of {name} is {value}"),
    "top_holdings": ("Top holdings", "Top {n} holdings of {name} by portfolio weight: {value}"),
}


def build_sections(fields: dict[str, dict], scheme_name: str) -> list[dict]:
    """One short, self-contained section per fact. Each sentence names the scheme, so a
    retrieved section makes sense on its own."""
    sections = []
    for key, (heading, template) in _TEMPLATES.items():
        f = fields.get(key)
        if not f:
            continue
        text = template.format(name=scheme_name, value=f["value"], n=TOP_HOLDINGS)
        if f.get("as_on"):
            text += f" (as on {display_date(f['as_on'])})"
        sections.append({
            "field": key,
            "heading": heading,
            "text": text.rstrip(".") + ".",
            "as_on": f.get("as_on"),
            "volatile": f.get("volatile", False),
        })
    return sections


def content_hash(sections: list[dict]) -> str:
    """Hash of the non-volatile sections only, so daily NAV/AUM changes don't count as a
    content change (edge case ING-06)."""
    stable = [{"heading": s["heading"], "text": s["text"]} for s in sections if not s["volatile"]]
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode("utf-8")).hexdigest()


def _visible_text(html: str) -> str:
    import trafilatura

    return clean_text(trafilatura.extract(html) or "")


def parse_html(html: str, source: Any, meta: dict, scheme_name: str) -> dict:
    """Parse one saved page into the document saved at data/parsed/<source_id>.json."""
    data = extract_next_data(html)
    if data is not None:
        name = clean_text(data.get("scheme_name")) or scheme_name
        fields = extract_fields(data)
        sections = build_sections(fields, name)
        extraction = "next_data"
        doc_date = iso_date(data.get("nav_date"))
    else:
        name, fields, extraction, doc_date = scheme_name, {}, "text_fallback", None
        text = _visible_text(html)
        sections = [{"field": None, "heading": "Page text", "text": text,
                     "as_on": None, "volatile": False}] if text else []

    return {
        "source_id": source.id,
        "scheme": source.scheme,
        "scheme_name": name,
        "url": source.url,
        "source_type": source.source_type,
        "fetched_at": meta.get("fetched_at"),
        "doc_date": doc_date,
        "extraction": extraction,
        "fields": fields,
        "missing_required": [f for f in REQUIRED_FIELDS if f not in fields],
        "sections": sections,
        "content_hash": content_hash(sections),
    }
