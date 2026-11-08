"""Quality gates for freshly parsed data (implementation-plan.md, Phase 7, task 4).

Run before a new index version is staged. Any problem stops the refresh, and the live
version stays in place. (The retrieval eval on the staged version is the second gate,
run by the workflow: `python -m tests.eval.retrieval_eval --version <v>`.)
"""

import re
from datetime import date, timedelta

from app.ingest.parse import REQUIRED_FIELDS

# SEBI riskometer levels.
RISK_LEVELS = {"low", "low to moderate", "moderate", "moderately high", "high", "very high"}
MAX_EXPENSE_RATIO = 3.0  # % (TER caps for equity schemes are around 2.25%)


def _number(text: str) -> float | None:
    match = re.search(r"\d[\d,]*(?:\.\d+)?", text or "")
    return float(match.group(0).replace(",", "")) if match else None


def check_docs(docs: list[dict], expected_sources: int, today: date | None = None,
               max_age_days: int | None = 7) -> list[str]:
    """Return human-readable problems; an empty list means the data may be staged.
    `max_age_days=None` skips the freshness check (offline rebuild from old snapshots)."""
    today = today or date.today()
    problems = []
    if len(docs) != expected_sources:
        problems.append(f"parsed {len(docs)} of {expected_sources} sources")
    for doc in docs:
        name = doc.get("source_id", "?")
        fields = doc.get("fields", {})
        missing = [f for f in REQUIRED_FIELDS if f not in fields]
        if missing:
            problems.append(f"{name}: missing {', '.join(missing)}")

        if "expense_ratio" in fields:
            value = _number(fields["expense_ratio"]["value"])
            if value is None or not 0 < value <= MAX_EXPENSE_RATIO:
                problems.append(f"{name}: expense ratio {fields['expense_ratio']['value']!r} "
                                f"is outside 0–{MAX_EXPENSE_RATIO}%")
        for key in ("min_sip", "min_lumpsum"):
            if key in fields:
                value = _number(fields[key]["value"])
                if value is None or value <= 0 or "₹" not in fields[key]["value"]:
                    problems.append(f"{name}: {key} {fields[key]['value']!r} is not a "
                                    "positive ₹ amount")
        if "riskometer" in fields and fields["riskometer"]["value"].strip().lower() \
                not in RISK_LEVELS:
            problems.append(f"{name}: riskometer {fields['riskometer']['value']!r} is not a "
                            "SEBI level")
        if "exit_load" in fields and not fields["exit_load"]["value"].strip():
            problems.append(f"{name}: exit load is empty")

        doc_date = doc.get("doc_date")
        if not doc_date:
            problems.append(f"{name}: no data date on the page")
        else:
            d = date.fromisoformat(doc_date)
            if max_age_days is not None and d < today - timedelta(days=max_age_days):
                problems.append(f"{name}: page data is from {doc_date}, more than "
                                f"{max_age_days} days old")
            elif d > today + timedelta(days=1):
                problems.append(f"{name}: page data date {doc_date} is in the future")
    return problems
